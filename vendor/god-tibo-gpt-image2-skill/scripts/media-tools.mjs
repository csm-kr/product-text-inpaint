import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import {
  delimiter,
  dirname,
  isAbsolute,
  relative,
  resolve,
  sep,
} from "node:path";


function colorHex(rgb) {
  return rgb.map((value) => value.toString(16).padStart(2, "0")).join("");
}


export function buildPadFilter(plan, rgb) {
  return [
    `pad=${plan.side}:${plan.side}`,
    `${plan.x_offset}:${plan.y_offset}`,
    `color=0x${colorHex(rgb)}`,
  ].join(":");
}


export function buildAlignCropFilter(plan) {
  return [
    `scale=${plan.side}:${plan.side}:flags=lanczos`,
    `crop=${plan.width}:${plan.height}:${plan.x_offset}:${plan.y_offset}`,
  ].join(",");
}


export function buildCenterCropResizeFilter(plan) {
  return [
    `crop=${plan.crop_width}:${plan.crop_height}:${plan.x_offset}:${plan.y_offset}`,
    `scale=${plan.target_width}:${plan.target_height}:flags=lanczos`,
  ].join(",");
}


export function buildGifFilter({ width = 768, fps = 8 } = {}) {
  return [
    `[0:v]fps=${fps},scale=${width}:-2:flags=lanczos,split[s0][s1]`,
    "[s0]palettegen=stats_mode=full[p]",
    "[s1][p]paletteuse=dither=sierra2_4a",
  ].join(";");
}


export function mediaPathForContainer(repositoryRoot, filePath) {
  const root = resolve(repositoryRoot);
  const absolute = resolve(filePath);
  const rel = relative(root, absolute);
  if (rel.startsWith("..") || rel === "") {
    throw new Error("ComfyUI 컨테이너 fallback은 input 또는 output 경로만 허용합니다.");
  }
  const [first, ...rest] = rel.split(sep);
  if (!["input", "output"].includes(first)) {
    throw new Error("ComfyUI 컨테이너 fallback은 input 또는 output 경로만 허용합니다.");
  }
  return ["/app", first, ...rest].join("/");
}


function runCommand(command, args, { capture = true } = {}) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(command, args, {
      stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
      windowsHide: true,
    });
    const stdout = [];
    const stderr = [];
    child.stdout?.on("data", (chunk) => stdout.push(chunk));
    child.stderr?.on("data", (chunk) => stderr.push(chunk));
    child.once("error", rejectPromise);
    child.once("close", (code) => {
      if (code !== 0) {
        rejectPromise(
          new Error(
            `${command} 실행 실패(${code}): ${Buffer.concat(stderr).toString("utf8").trim()}`,
          ),
        );
        return;
      }
      resolvePromise({
        stdout: Buffer.concat(stdout),
        stderr: Buffer.concat(stderr),
      });
    });
  });
}


async function hasExecutable(name) {
  const paths = (process.env.PATH ?? "").split(delimiter);
  const candidates =
    process.platform === "win32"
      ? [name, `${name}.exe`, `${name}.cmd`, `${name}.bat`]
      : [name];
  for (const directory of paths) {
    if (!directory) continue;
    for (const candidate of candidates) {
      try {
        await access(resolve(directory, candidate));
        return true;
      } catch {
        // 다음 실행 파일 후보를 확인한다.
      }
    }
  }
  return false;
}


async function runMediaCommand(tool, args, options = {}) {
  if (await hasExecutable(tool)) {
    return runCommand(tool, args, options);
  }
  const repositoryRoot = options.comfyuiRoot;
  if (!repositoryRoot) {
    throw new Error(`${tool}이 PATH에 없고 comfyui_root도 지정되지 않았습니다.`);
  }
  const mapped = args.map((value, index) => {
    if (
      typeof value === "string" &&
      isAbsolute(value) &&
      (index === args.length - 1 || args[index - 1] === "-i")
    ) {
      return mediaPathForContainer(repositoryRoot, value);
    }
    return value;
  });
  return runCommand(
    "docker",
    ["exec", "comfyui", tool, ...mapped],
    options,
  );
}


export async function inspectImage(filePath, options = {}) {
  const result = await runMediaCommand(
    "ffprobe",
    [
      "-v",
      "error",
      "-select_streams",
      "v:0",
      "-show_entries",
      "stream=width,height",
      "-of",
      "json",
      resolve(filePath),
    ],
    options,
  );
  const payload = JSON.parse(result.stdout.toString("utf8"));
  const stream = payload.streams?.[0];
  if (!stream?.width || !stream?.height) {
    throw new Error(`이미지 크기를 확인하지 못했습니다: ${filePath}`);
  }
  return { width: stream.width, height: stream.height };
}


export async function averageRgb(filePath, options = {}) {
  const result = await runMediaCommand(
    "ffmpeg",
    [
      "-v",
      "error",
      "-i",
      resolve(filePath),
      "-vf",
      "scale=1:1:flags=bilinear,format=rgb24",
      "-frames:v",
      "1",
      "-f",
      "rawvideo",
      "-",
    ],
    options,
  );
  if (result.stdout.length < 3) {
    throw new Error(`평균 RGB를 계산하지 못했습니다: ${filePath}`);
  }
  return [...result.stdout.subarray(0, 3)];
}


export async function padToSquare({
  inputPath,
  outputPath,
  plan,
  color,
  comfyuiRoot,
}) {
  await runMediaCommand(
    "ffmpeg",
    [
      "-y",
      "-v",
      "error",
      "-i",
      resolve(inputPath),
      "-vf",
      `${buildPadFilter(plan, color)},format=rgb24`,
      "-frames:v",
      "1",
      resolve(outputPath),
    ],
    { comfyuiRoot },
  );
  return resolve(outputPath);
}


export async function alignAndCrop({
  inputPath,
  outputPath,
  plan,
  comfyuiRoot,
}) {
  await runMediaCommand(
    "ffmpeg",
    [
      "-y",
      "-v",
      "error",
      "-i",
      resolve(inputPath),
      "-vf",
      buildAlignCropFilter(plan),
      "-frames:v",
      "1",
      resolve(outputPath),
    ],
    { comfyuiRoot },
  );
  return resolve(outputPath);
}


export async function centerCropAndResize({
  inputPath,
  outputPath,
  plan,
  comfyuiRoot,
}) {
  await runMediaCommand(
    "ffmpeg",
    [
      "-y",
      "-v",
      "error",
      "-i",
      resolve(inputPath),
      "-vf",
      buildCenterCropResizeFilter(plan),
      "-frames:v",
      "1",
      resolve(outputPath),
    ],
    { comfyuiRoot },
  );
  return resolve(outputPath);
}


export async function createGif({
  framePaths,
  outputPath,
  fps = 8,
  width = 768,
  comfyuiRoot,
}) {
  if (!Array.isArray(framePaths) || framePaths.length === 0) {
    throw new Error("GIF를 만들 프레임이 없습니다.");
  }
  const concatPath = resolve(dirname(outputPath), "gif-frames.txt");
  const { writeFile } = await import("node:fs/promises");
  const useContainer = !(await hasExecutable("ffmpeg"));
  if (useContainer && !comfyuiRoot) {
    throw new Error("ffmpeg이 PATH에 없고 comfyui_root도 지정되지 않았습니다.");
  }
  const lines = framePaths
    .map((framePath) => {
      const mediaPath = useContainer
        ? mediaPathForContainer(comfyuiRoot, framePath)
        : resolve(framePath);
      return `file '${mediaPath.replaceAll("'", "'\\''")}'`;
    })
    .join("\n");
  await writeFile(concatPath, `${lines}\n`, "utf8");
  await runMediaCommand(
    "ffmpeg",
    [
      "-y",
      "-v",
      "error",
      "-f",
      "concat",
      "-safe",
      "0",
      "-r",
      String(fps),
      "-i",
      concatPath,
      "-filter_complex",
      buildGifFilter({ width, fps }),
      "-loop",
      "0",
      resolve(outputPath),
    ],
    { comfyuiRoot },
  );
  return resolve(outputPath);
}
