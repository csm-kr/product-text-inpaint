#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  mkdir,
  readFile,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import { extname, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  alignAndCrop,
  averageRgb,
  centerCropAndResize,
  createGif,
  inspectImage,
  padToSquare,
} from "./media-tools.mjs";
import { generatePrivateCodexImage } from "./private-codex-client.mjs";


const DETAIL_INSTRUCTIONS = {
  1: [
    "Detail mode: Clean Simplified.",
    "Use broad clean shapes, smooth gradients, and restrained texture.",
    "Remove speckles, glitter-like noise, micro-contrast, gritty grain, and incidental tiny marks.",
  ].join(" "),
  2: [
    "Detail mode: Balanced Detail.",
    "Keep important material and subject detail while simplifying incidental micro-texture.",
    "Avoid speckles, glitter-like noise, harsh micro-contrast, and gritty grain.",
  ].join(" "),
  3: [
    "Detail mode: Rich Detail.",
    "Preserve meaningful fine structure and material cues with coherent, non-random texture.",
    "Do not add speckles, glitter-like noise, crunchy sharpening, or chaotic micro-contrast.",
  ].join(" "),
};

const INVARIANT_REFERENCE_INSTRUCTION = [
  "Image 1 is the canonical base image and determines subject identity, composition, camera, and layout.",
  "Images 2 and later are supporting references only.",
  "Apply only the requested change.",
  "Do not zoom, crop, reframe, rotate, or change camera perspective.",
  "Keep all unrequested regions and the padding area unchanged.",
].join(" ");

const CONTROLLABLE_REFERENCE_INSTRUCTION = [
  "Image 1 is the canonical identity and content reference.",
  "Images 2 and later are supporting references only.",
  "Apply only the requested change.",
  "The requested target canvas overrides Image 1's pixel dimensions and aspect ratio.",
  "Compose for the target canvas, keep essential content near the center-crop-safe area, and avoid unrelated changes.",
].join(" ");

const SIZE_MODES = new Set(["invariant", "controllable"]);
export const MAX_JOB_IMAGES = 64;
export const MAX_WORKERS = 32;
export const DEFAULT_BATCH_SIZE = 24;
export const DEFAULT_WORKERS = 24;


function integerInRange(value, min, max, label) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${label}는 ${min}~${max} 정수여야 합니다.`);
  }
  return value;
}


function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}


export function parseTargetSize(value, label = "target_size") {
  const match = typeof value === "string"
    ? value.trim().match(/^(\d{1,5})\s*[x×]\s*(\d{1,5})$/i)
    : null;
  if (!match) {
    throw new Error(`${label}는 WIDTHxHEIGHT 형식이어야 합니다.`);
  }
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (width < 1 || height < 1 || width > 16384 || height > 16384) {
    throw new Error(`${label}의 가로·세로는 각각 1~16384여야 합니다.`);
  }
  return {
    width,
    height,
    text: `${width}x${height}`,
    display: `${width}×${height}`,
  };
}


function resolveSizeContract(source, references) {
  let sizeMode = source.size_mode;
  let targetSizeValue = source.target_size;
  let usedLegacyFields = false;

  if (sizeMode == null && hasOwn(source, "size_invariant")) {
    usedLegacyFields = true;
    sizeMode = source.size_invariant ? "invariant" : "controllable";
    if (
      sizeMode === "controllable" &&
      targetSizeValue == null &&
      source.api_size != null
    ) {
      targetSizeValue = source.api_size;
    }
  }

  if (!SIZE_MODES.has(sizeMode)) {
    throw new Error(
      'size_mode은 "invariant" 또는 "controllable"로 명시해야 합니다. 생성 전에 사용자에게 출력 크기를 확인하세요.',
    );
  }
  if (sizeMode === "invariant") {
    if (references.length === 0) {
      throw new Error(
        'size_mode "invariant"는 첫 reference 이미지가 필요합니다. reference가 없으면 사용자에게 목표 W×H를 확인하고 "controllable"을 사용하세요.',
      );
    }
    if (targetSizeValue != null) {
      throw new Error(
        'size_mode "invariant"에서는 target_size를 지정하지 않습니다. 첫 reference의 원본 크기를 사용합니다.',
      );
    }
    return {
      sizeMode,
      targetSize: null,
      usedLegacyFields,
    };
  }

  if (targetSizeValue == null) {
    throw new Error(
      'size_mode "controllable"에는 target_size가 필요합니다. 생성 전에 사용자에게 목표 W×H를 확인하세요.',
    );
  }
  return {
    sizeMode,
    targetSize: parseTargetSize(targetSizeValue),
    usedLegacyFields,
  };
}


export function buildDetailPrompt({
  prompt,
  detailLevel = 2,
  hasReferences = false,
  sizeMode,
  targetSize,
  referenceSize = null,
}) {
  const detail = DETAIL_INSTRUCTIONS[detailLevel];
  if (!detail) {
    throw new Error("detail_level은 1, 2, 3 중 하나여야 합니다.");
  }
  if (!SIZE_MODES.has(sizeMode) || !targetSize) {
    throw new Error("프롬프트를 만들려면 유효한 sizeMode와 targetSize가 필요합니다.");
  }
  const referenceInstruction = hasReferences
    ? (
      sizeMode === "invariant"
        ? INVARIANT_REFERENCE_INSTRUCTION
        : CONTROLLABLE_REFERENCE_INSTRUCTION
    )
    : "";
  const sizeInstruction = sizeMode === "invariant"
    ? [
      `Final output size: exactly ${targetSize.display} pixels, matching Image 1's original pixel dimensions.`,
      "Preserve Image 1's original aspect ratio, composition, camera, and framing.",
    ].join(" ")
    : [
      `Target output size: exactly ${targetSize.display} pixels.`,
      `Compose for the ${targetSize.width}:${targetSize.height} target aspect ratio.`,
      "The generated canvas will be minimally center-cropped to that aspect ratio and resized with LANCZOS, so keep essential content inside the center-crop-safe area.",
      referenceSize
        ? `Image 1's original size is ${referenceSize.width}×${referenceSize.height} pixels.`
        : "",
    ].filter(Boolean).join(" ");
  return [
    detail,
    referenceInstruction,
    sizeInstruction,
    `User request: ${prompt.trim()}`,
  ].filter(Boolean).join("\n\n");
}


function validateReferences(value, label = "references") {
  const references = value ?? [];
  if (
    !Array.isArray(references) ||
    references.length > 16 ||
    !references.every((reference) => typeof reference === "string" && reference)
  ) {
    throw new Error(`${label}는 최대 16개의 이미지 경로 배열이어야 합니다.`);
  }
  return [...references];
}


export function validateJob(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw new Error("작업 설정은 JSON 객체여야 합니다.");
  }
  const hasPrompt = typeof source.prompt === "string" && source.prompt.trim();
  const hasPrompts = Array.isArray(source.prompts);
  const hasItems = Array.isArray(source.items);
  if ([Boolean(hasPrompt), hasPrompts, hasItems].filter(Boolean).length !== 1) {
    throw new Error("prompt, prompts, items 중 하나만 지정해야 합니다.");
  }

  let prompts;
  let items;
  let mode;
  if (hasItems) {
    if (
      source.items.length === 0 ||
      source.items.length > MAX_JOB_IMAGES
    ) {
      throw new Error(
        `items는 1개 이상 최대 ${MAX_JOB_IMAGES}개까지 허용합니다.`,
      );
    }
    if (source.references != null) {
      throw new Error(
        "items 모드에서는 최상위 references 대신 각 items[].references를 사용하세요.",
      );
    }
    items = source.items.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        throw new Error(`items[${index}]는 객체여야 합니다.`);
      }
      if (typeof item.prompt !== "string" || !item.prompt.trim()) {
        throw new Error(`items[${index}].prompt는 비어 있지 않은 문자열이어야 합니다.`);
      }
      return {
        prompt: item.prompt.trim(),
        references: validateReferences(
          item.references,
          `items[${index}].references`,
        ),
      };
    });
    prompts = null;
    mode = "per_item";
  } else if (hasPrompts) {
    if (
      source.prompts.length === 0 ||
      source.prompts.length > MAX_JOB_IMAGES
    ) {
      throw new Error(
        `prompts는 1개 이상 최대 ${MAX_JOB_IMAGES}개까지 허용합니다.`,
      );
    }
    if (
      !source.prompts.every(
        (prompt) => typeof prompt === "string" && prompt.trim(),
      )
    ) {
      throw new Error("prompts의 모든 항목은 비어 있지 않은 문자열이어야 합니다.");
    }
    prompts = source.prompts.map((prompt) => prompt.trim());
    items = null;
    mode = "per_prompt";
  } else {
    prompts = null;
    items = null;
    mode = "shared_prompt";
  }

  const references = mode === "per_item"
    ? []
    : validateReferences(source.references);
  const sizeContracts = mode === "per_item"
    ? items.map((item) => resolveSizeContract(source, item.references))
    : [resolveSizeContract(source, references)];
  const sizeContract = sizeContracts[0];
  const batchSize = mode === "per_prompt"
    ? prompts.length
    : mode === "per_item"
      ? items.length
      : integerInRange(
        source.batch_size ?? DEFAULT_BATCH_SIZE,
        1,
        MAX_JOB_IMAGES,
        "batch_size",
      );
  const requestedWorkers = integerInRange(
    source.workers ?? Math.min(DEFAULT_WORKERS, batchSize),
    1,
    MAX_WORKERS,
    "workers",
  );
  const workers = Math.min(requestedWorkers, batchSize);
  const detailLevel = integerInRange(
    source.detail_level ?? 2,
    1,
    3,
    "detail_level",
  );
  if (
    typeof source.output_dir !== "string" ||
    !source.output_dir.trim()
  ) {
    throw new Error("output_dir이 필요합니다.");
  }

  return {
    ...source,
    prompt: mode === "shared_prompt" ? source.prompt.trim() : undefined,
    prompts,
    items,
    references: [...references],
    workers,
    batch_size: batchSize,
    detail_level: detailLevel,
    output_dir: source.output_dir,
    size_mode: sizeContract.sizeMode,
    target_size: sizeContract.targetSize,
    used_legacy_size_fields: sizeContract.usedLegacyFields,
    mode,
  };
}


export function buildGenerationTasks(job, prepared) {
  if (job.mode === "shared_prompt") {
    return [{
      index: 0,
      body: {
        prompt: buildDetailPrompt({
          prompt: job.prompt,
          detailLevel: job.detail_level,
          hasReferences: prepared.images.length > 0,
          sizeMode: job.size_mode,
          targetSize: prepared.expectedSize,
          referenceSize: prepared.referenceSize,
        }),
        reference_images: prepared.images,
        batch_size: job.batch_size,
        workers: Math.min(job.workers, job.batch_size),
      },
    }];
  }
  if (job.mode === "per_item") {
    return job.items.map((item, index) => {
      const itemPrepared = prepared[index];
      return {
        index,
        prepared: itemPrepared,
        body: {
          prompt: buildDetailPrompt({
            prompt: item.prompt,
            detailLevel: job.detail_level,
            hasReferences: itemPrepared.images.length > 0,
            sizeMode: job.size_mode,
            targetSize: itemPrepared.expectedSize,
            referenceSize: itemPrepared.referenceSize,
          }),
          reference_images: itemPrepared.images,
          batch_size: 1,
          workers: 1,
        },
      };
    });
  }
  return job.prompts.map((prompt, index) => ({
    index,
    body: {
      prompt: buildDetailPrompt({
        prompt,
        detailLevel: job.detail_level,
        hasReferences: prepared.images.length > 0,
        sizeMode: job.size_mode,
        targetSize: prepared.expectedSize,
        referenceSize: prepared.referenceSize,
      }),
      reference_images: prepared.images,
      batch_size: 1,
      workers: 1,
    },
  }));
}


export async function runWorkerPool(items, workers, handler) {
  if (!Array.isArray(items)) {
    throw new Error("worker pool 입력은 배열이어야 합니다.");
  }
  if (items.length === 0) return [];
  const count = integerInRange(
    Math.min(workers, items.length),
    1,
    MAX_WORKERS,
    "workers",
  );
  const results = Array(items.length);
  let nextIndex = 0;

  async function runWorker() {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await handler(items[index], index);
    }
  }

  await Promise.all(Array.from({ length: count }, () => runWorker()));
  return results;
}


export function createSizeInvariantPlan(width, height) {
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1
  ) {
    throw new Error("Size Invariant 입력 크기는 양의 정수여야 합니다.");
  }
  const side = Math.max(width, height);
  return {
    width,
    height,
    side,
    api_side: side <= 1024 ? 1024 : 2048,
    x_offset: Math.floor((side - width) / 2),
    y_offset: Math.floor((side - height) / 2),
    padding_needed: width !== height,
  };
}


export function createControllablePlan(
  sourceWidth,
  sourceHeight,
  targetWidth,
  targetHeight,
) {
  for (const [value, label] of [
    [sourceWidth, "생성 원본 가로"],
    [sourceHeight, "생성 원본 세로"],
    [targetWidth, "목표 가로"],
    [targetHeight, "목표 세로"],
  ]) {
    if (!Number.isInteger(value) || value < 1) {
      throw new Error(`${label}는 양의 정수여야 합니다.`);
    }
  }

  let cropWidth = sourceWidth;
  let cropHeight = sourceHeight;
  if (sourceWidth * targetHeight > sourceHeight * targetWidth) {
    cropWidth = Math.max(
      1,
      Math.min(sourceWidth, Math.round(sourceHeight * targetWidth / targetHeight)),
    );
  } else if (sourceWidth * targetHeight < sourceHeight * targetWidth) {
    cropHeight = Math.max(
      1,
      Math.min(sourceHeight, Math.round(sourceWidth * targetHeight / targetWidth)),
    );
  }
  return {
    source_width: sourceWidth,
    source_height: sourceHeight,
    target_width: targetWidth,
    target_height: targetHeight,
    crop_width: cropWidth,
    crop_height: cropHeight,
    x_offset: Math.floor((sourceWidth - cropWidth) / 2),
    y_offset: Math.floor((sourceHeight - cropHeight) / 2),
  };
}


export function complementColor(rgb) {
  if (
    !Array.isArray(rgb) ||
    rgb.length !== 3 ||
    rgb.some((value) => !Number.isInteger(value) || value < 0 || value > 255)
  ) {
    throw new Error("RGB는 0~255 정수 세 개여야 합니다.");
  }
  return rgb.map((value) => 255 - value);
}


function mimeType(filePath) {
  const extension = extname(filePath).toLowerCase();
  const types = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
  };
  const type = types[extension];
  if (!type) {
    throw new Error(`지원하지 않는 reference 이미지 형식입니다: ${extension}`);
  }
  return type;
}


async function imageDataUrl(filePath) {
  const bytes = await readFile(filePath);
  return `data:${mimeType(filePath)};base64,${bytes.toString("base64")}`;
}


async function sha256(filePath) {
  const bytes = await readFile(filePath);
  return createHash("sha256").update(bytes).digest("hex");
}


async function prepareReferences(
  job,
  baseDirectory,
  outputDirectory,
  artifactSuffix = "",
) {
  const originalPaths = job.references.map((reference) =>
    resolve(baseDirectory, reference)
  );
  for (const filePath of originalPaths) {
    const fileStat = await stat(filePath);
    if (!fileStat.isFile() || fileStat.size === 0) {
      throw new Error(`reference가 유효한 파일이 아닙니다: ${filePath}`);
    }
  }

  const referenceSize = originalPaths.length > 0
    ? await inspectImage(originalPaths[0], {
      comfyuiRoot: job.comfyui_root,
    })
    : null;

  if (job.size_mode === "controllable") {
    const expectedSize = job.target_size;
    return {
      apiSize: "auto",
      invariantPlan: null,
      padColor: null,
      originalPaths,
      paths: originalPaths,
      images: await Promise.all(originalPaths.map(imageDataUrl)),
      referenceSize,
      expectedSize,
      referenceMatchesTarget: referenceSize
        ? (
          referenceSize.width === expectedSize.width &&
          referenceSize.height === expectedSize.height
        )
        : null,
    };
  }

  const dimensions = referenceSize;
  const plan = createSizeInvariantPlan(
    dimensions.width,
    dimensions.height,
  );
  let basePath = originalPaths[0];
  let padColor = null;
  if (plan.padding_needed) {
    padColor = complementColor(
      await averageRgb(originalPaths[0], { comfyuiRoot: job.comfyui_root }),
    );
    basePath = join(
      outputDirectory,
      `reference-size-invariant${artifactSuffix}.png`,
    );
    await padToSquare({
      inputPath: originalPaths[0],
      outputPath: basePath,
      plan,
      color: padColor,
      comfyuiRoot: job.comfyui_root,
    });
  }

  const paths = [basePath, ...originalPaths.slice(1)];
  const expectedSize = {
    width: dimensions.width,
    height: dimensions.height,
    text: `${dimensions.width}x${dimensions.height}`,
    display: `${dimensions.width}×${dimensions.height}`,
  };
  return {
    apiSize: `${plan.api_side}x${plan.api_side}`,
    invariantPlan: plan,
    padColor,
    originalPaths,
    paths,
    images: await Promise.all(paths.map(imageDataUrl)),
    referenceSize,
    expectedSize,
    referenceMatchesTarget: true,
  };
}


async function finalizeImage({
  rawPath,
  finalPath,
  sizeMode,
  invariantPlan,
  expectedSize,
  comfyuiRoot,
}) {
  const rawSize = await inspectImage(rawPath, { comfyuiRoot });
  let postprocessPlan;
  if (sizeMode === "invariant") {
    postprocessPlan = invariantPlan;
    await alignAndCrop({
      inputPath: rawPath,
      outputPath: finalPath,
      plan: invariantPlan,
      comfyuiRoot,
    });
  } else {
    postprocessPlan = createControllablePlan(
      rawSize.width,
      rawSize.height,
      expectedSize.width,
      expectedSize.height,
    );
    await centerCropAndResize({
      inputPath: rawPath,
      outputPath: finalPath,
      plan: postprocessPlan,
      comfyuiRoot,
    });
  }
  const finalSize = await inspectImage(finalPath, { comfyuiRoot });
  if (
    finalSize.width !== expectedSize.width ||
    finalSize.height !== expectedSize.height
  ) {
    throw new Error(
      `출력 크기 검증 실패: 기대 ${expectedSize.display}, 실제 ${finalSize.width}×${finalSize.height}`,
    );
  }
  await unlink(rawPath);
  return {
    rawSize,
    finalSize,
    postprocessPlan,
    matchesExpected: true,
  };
}


async function generateOne({
  task,
  frameIndex,
  outputDirectory,
  sizeMode,
  prepared,
  dryRun,
  comfyuiRoot,
  generator,
}) {
  const suffix = String(frameIndex).padStart(3, "0");
  const finalPath = join(outputDirectory, `frame-${suffix}.png`);
  const rawPath = join(outputDirectory, `raw-${suffix}.png`);
  const startedAt = performance.now();
  const generated = await generator({
    prompt: task.body.prompt,
    images: task.body.reference_images,
    apiSize: prepared.apiSize,
    outputPath: rawPath,
    dryRun,
  });
  let finalized = null;
  if (!dryRun) {
    finalized = await finalizeImage({
      rawPath,
      finalPath,
      sizeMode,
      invariantPlan: prepared.invariantPlan,
      expectedSize: prepared.expectedSize,
      comfyuiRoot,
    });
  }
  const matchesReference = finalized && prepared.referenceSize
    ? (
      finalized.finalSize.width === prepared.referenceSize.width &&
      finalized.finalSize.height === prepared.referenceSize.height
    )
    : null;
  return {
    index: frameIndex,
    path: finalPath,
    size_mode: sizeMode,
    reference_paths: prepared.originalPaths,
    prompt: task.body.prompt,
    duration_ms: Math.round(performance.now() - startedAt),
    response_id: generated.responseId,
    revised_prompt: generated.revisedPrompt,
    warnings: generated.warnings,
    sha256: dryRun ? null : await sha256(finalPath),
    raw_size: finalized?.rawSize ?? null,
    final_size: finalized?.finalSize ?? null,
    size_check: dryRun
      ? null
      : {
        expected: {
          width: prepared.expectedSize.width,
          height: prepared.expectedSize.height,
        },
        matches_expected: finalized.matchesExpected,
        matches_reference: matchesReference,
      },
    postprocess_plan: finalized?.postprocessPlan ?? null,
    request: dryRun ? generated.request : undefined,
  };
}


function sizeManifestEntry(job, prepared) {
  return {
    mode: job.size_mode,
    target: {
      width: prepared.expectedSize.width,
      height: prepared.expectedSize.height,
    },
    reference: prepared.referenceSize,
    target_matches_reference: prepared.referenceMatchesTarget,
    backend_request_size: prepared.apiSize,
    postprocess: job.size_mode === "invariant"
      ? "size-invariant-align-center-crop"
      : "minimal-center-crop-lanczos-resize",
    legacy_fields_migrated: job.used_legacy_size_fields,
  };
}


export async function runJob(
  source,
  {
    baseDirectory = process.cwd(),
    dryRun = false,
    generator = generatePrivateCodexImage,
  } = {},
) {
  const job = validateJob(source);
  const outputDirectory = resolve(baseDirectory, job.output_dir);
  const comfyuiRoot = job.comfyui_root
    ? resolve(baseDirectory, job.comfyui_root)
    : undefined;
  const runtimeJob = { ...job, comfyui_root: comfyuiRoot };
  await mkdir(outputDirectory, { recursive: true });
  const startedAt = performance.now();
  const prepared = job.mode === "per_item"
    ? await Promise.all(
      job.items.map((item, index) =>
        prepareReferences(
          { ...runtimeJob, references: item.references },
          baseDirectory,
          outputDirectory,
          `-${String(index).padStart(3, "0")}`,
        )
      ),
    )
    : await prepareReferences(
      runtimeJob,
      baseDirectory,
      outputDirectory,
    );
  const tasks = buildGenerationTasks(job, prepared);
  let images;

  if (job.mode === "shared_prompt") {
    const task = tasks[0];
    const indexes = Array.from(
      { length: task.body.batch_size },
      (_, index) => index,
    );
    images = await runWorkerPool(
      indexes,
      task.body.workers,
      (_, frameIndex) => generateOne({
        task,
        frameIndex,
        outputDirectory,
        sizeMode: job.size_mode,
        prepared,
        dryRun,
        comfyuiRoot,
        generator,
      }),
    );
  } else {
    images = await runWorkerPool(
      tasks,
      job.workers,
      (task) => generateOne({
        task,
        frameIndex: task.index,
        outputDirectory,
        sizeMode: job.size_mode,
        prepared: task.prepared ?? prepared,
        dryRun,
        comfyuiRoot,
        generator,
      }),
    );
  }

  let gifPath = null;
  if (!dryRun && job.gif) {
    const gifOptions = typeof job.gif === "object" ? job.gif : {};
    gifPath = resolve(
      outputDirectory,
      gifOptions.filename ?? "animation.gif",
    );
    await createGif({
      framePaths: images.map((image) => image.path),
      outputPath: gifPath,
      fps: gifOptions.fps ?? 8,
      width: gifOptions.width ?? 768,
      comfyuiRoot,
    });
  }

  const perItem = job.mode === "per_item";
  const preparedList = perItem ? prepared : [prepared];
  const manifest = {
    schema_version: 2,
    created_at: new Date().toISOString(),
    dry_run: dryRun,
    mode: job.mode,
    detail_level: job.detail_level,
    workers: job.workers,
    batch_size: job.batch_size,
    duration_ms: Math.round(performance.now() - startedAt),
    reference_paths: perItem
      ? preparedList.map((item) => item.originalPaths)
      : prepared.originalPaths,
    transmitted_reference_paths: perItem
      ? preparedList.map((item) => item.paths)
      : prepared.paths,
    api_size: perItem
      ? preparedList.map((item) => item.apiSize)
      : prepared.apiSize,
    size: perItem
      ? {
        mode: "per_item",
        items: preparedList.map((item) => sizeManifestEntry(job, item)),
      }
      : sizeManifestEntry(job, prepared),
    size_invariant: perItem
      ? preparedList.map((item) => item.invariantPlan)
      : prepared.invariantPlan,
    padding_color: perItem
      ? preparedList.map((item) => item.padColor)
      : prepared.padColor,
    images,
    gif_path: gifPath,
  };
  await writeFile(
    join(outputDirectory, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
  return manifest;
}


function parseArguments(argv) {
  let jobPath = null;
  let dryRun = false;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--job") {
      jobPath = argv[index + 1];
      index += 1;
    } else if (argument === "--dry-run") {
      dryRun = true;
    } else {
      throw new Error(`알 수 없는 인자입니다: ${argument}`);
    }
  }
  if (!jobPath) {
    throw new Error("사용법: node scripts/tibo-batch.mjs --job <job.json> [--dry-run]");
  }
  return { jobPath: resolve(jobPath), dryRun };
}


async function main() {
  const { jobPath, dryRun } = parseArguments(process.argv.slice(2));
  const source = JSON.parse(await readFile(jobPath, "utf8"));
  const manifest = await runJob(source, {
    baseDirectory: dirname(jobPath),
    dryRun,
  });
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);
}


const entryPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (entryPath === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}
