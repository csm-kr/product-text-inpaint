const DEFAULT_TIMEOUT_MS = 300_000;
const MAX_ATTEMPTS = 3;


function isSse(body, contentType) {
  const trimmed = body.trimStart();
  return (
    contentType.includes("text/event-stream") ||
    trimmed.startsWith("event:") ||
    trimmed.startsWith("data:")
  );
}


function redactHeaders(headers = {}) {
  const sanitized = { ...headers };
  if (sanitized.Authorization) {
    sanitized.Authorization = "Bearer [REDACTED]";
  }
  if (sanitized["ChatGPT-Account-ID"]) {
    sanitized["ChatGPT-Account-ID"] = "[REDACTED_ACCOUNT_ID]";
  }
  if (sanitized.session_id) {
    sanitized.session_id = "[REDACTED_SESSION_ID]";
  }
  return sanitized;
}


function ensureReferenceImages(body, images) {
  if (!images?.length) return body;

  const input = Array.isArray(body.input) ? [...body.input] : [];
  if (input.length === 0) {
    input.push({
      type: "message",
      role: "user",
      content: [],
    });
  }
  const message = {
    ...input[0],
    content: Array.isArray(input[0]?.content) ? [...input[0].content] : [],
  };
  const existing = new Set(
    message.content
      .filter((block) => block?.type === "input_image")
      .map((block) => block.image_url),
  );
  for (const image of images) {
    if (!existing.has(image)) {
      message.content.push({ type: "input_image", image_url: image });
    }
  }
  input[0] = message;
  body.input = input;
  return body;
}


export function buildPrivateCodexRequest({
  library,
  baseUrl,
  session,
  prompt,
  model,
  originator,
  images = [],
  apiSize,
}) {
  if (
    apiSize !== "auto" &&
    !/^\d{2,5}x\d{2,5}$/.test(String(apiSize))
  ) {
    throw new Error(`검증되지 않은 GPT Image 2 API 크기입니다: ${apiSize}`);
  }

  const request = library.buildResponsesRequest({
    baseUrl,
    session,
    prompt,
    model,
    originator,
    images,
  });
  const tool = request.body?.tools?.find(
    (candidate) => candidate?.type === "image_generation",
  );
  if (!tool) {
    throw new Error("image_generation tool 요청을 구성하지 못했습니다.");
  }

  tool.size = apiSize;
  ensureReferenceImages(request.body, images);
  const sanitizeBody =
    library.sanitizeRequestBody ??
    ((body) => structuredClone(body));
  const sanitizedHeaders =
    library.sanitizeHeaders?.(request.headers) ??
    request.sanitized?.headers ??
    redactHeaders(request.headers);
  request.sanitized = {
    ...request.sanitized,
    headers: sanitizedHeaders,
    body: sanitizeBody(request.body),
  };
  return request;
}


export function parsePrivateCodexResponse({ library, body, contentType = "" }) {
  let parsed;
  if (isSse(body, contentType)) {
    parsed = library.parseSseText(body);
  } else {
    const payload = JSON.parse(body);
    parsed = {
      events: [],
      items: Array.isArray(payload?.output) ? payload.output : [],
      responseId: payload?.id ?? null,
    };
  }
  return {
    parsed,
    generation: library.extractImageGeneration(parsed),
  };
}


function retryDelayMilliseconds(response, attempt) {
  const retryAfter = response.headers?.get?.("retry-after");
  if (retryAfter && Number.isFinite(Number(retryAfter))) {
    return Math.max(0, Number(retryAfter) * 1_000);
  }
  return 1_000 * (attempt + 1);
}


async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}


async function fetchWithRetry({
  request,
  fetchImpl,
  sleep,
  timeoutMs,
}) {
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetchImpl(request.url, {
        method: "POST",
        headers: request.headers,
        body: JSON.stringify(request.body),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }

    if (response.ok) return response;
    const transient = response.status === 429 || response.status >= 500;
    const responseErrorText = await response.text();
    if (transient && attempt < MAX_ATTEMPTS - 1) {
      await sleep(retryDelayMilliseconds(response, attempt));
      continue;
    }
    if (response.status === 401) {
      throw new Error(
        "Codex 인증이 만료되었습니다. Codex에서 ChatGPT 로그인을 다시 수행하세요.",
      );
    }
    let serverMessage = "";
    try {
      const failure = JSON.parse(responseErrorText);
      const selected = failure?.error?.message ?? failure?.message ?? failure?.detail;
      if (typeof selected === "string") {
        serverMessage = selected.replace(/[A-Za-z0-9_\-]{80,}/g, "[redacted]").slice(0, 800);
      }
    } catch {}
    throw new Error(
      `비공개 Codex backend 요청이 HTTP ${response.status}로 실패했습니다.${serverMessage ? " Server: " + serverMessage : ""}`,
    );
  }
  throw new Error("비공개 Codex backend 재시도 한도를 초과했습니다.");
}


function responseHeadersToObject(headers) {
  if (!headers || typeof headers.entries !== "function") return {};
  return Object.fromEntries(headers.entries());
}


export async function generatePrivateCodexImage(args, dependencies = {}) {
  const library = dependencies.library ?? await import("god-tibo-imagen");
  const fetchImpl = dependencies.fetchImpl ?? globalThis.fetch;
  const sleep = dependencies.sleep ?? wait;
  if (typeof fetchImpl !== "function") {
    throw new Error("Node.js runtime에서 fetch를 사용할 수 없습니다.");
  }

  const config = library.resolveConfig({ provider: "private-codex" });
  const session = await library.loadCodexSession(config);
  const validation = library.validateCodexSession(session);
  const request = buildPrivateCodexRequest({
    library,
    baseUrl: config.baseUrl,
    session,
    prompt: args.prompt,
    model: config.defaultModel,
    originator: config.defaultOriginator,
    images: args.images,
    apiSize: args.apiSize,
  });

  if (args.dryRun) {
    return {
      outputPath: args.outputPath,
      request: request.sanitized,
      responseId: null,
      revisedPrompt: null,
      warnings: [
        ...(library.UNSUPPORTED_WARNING ? [library.UNSUPPORTED_WARNING] : []),
        ...(validation.warnings ?? []),
      ],
    };
  }

  const response = await fetchWithRetry({
    request,
    fetchImpl,
    sleep,
    timeoutMs: dependencies.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  });
  const body = await response.text();
  const { parsed, generation } = parsePrivateCodexResponse({
    library,
    body,
    contentType: response.headers?.get?.("content-type") ?? "",
  });
  await library.saveImage({
    resultBase64: generation.resultBase64,
    outputPath: args.outputPath,
  });
  return {
    outputPath: args.outputPath,
    responseId: parsed.responseId,
    revisedPrompt: generation.revisedPrompt,
    warnings: [
      ...(library.UNSUPPORTED_WARNING ? [library.UNSUPPORTED_WARNING] : []),
      ...(validation.warnings ?? []),
    ],
    response: {
      status: response.status,
      headers: responseHeadersToObject(response.headers),
    },
  };
}
