const TRACKER_BASE = "https://tracker.la-cale.space/announce?passkey=";
const SOURCE_VALUE = "La Cale reup";
const PASSKEY_STORAGE_KEY = "lacale.passkey";

const passkeyInput = document.getElementById("passkey");
const torrentInput = document.getElementById("torrentFile");
const processBtn = document.getElementById("processBtn");
const statusEl = document.getElementById("status");
const detailsEl = document.getElementById("details");
const currentNameEl = document.getElementById("currentName");
const changedNameEl = document.getElementById("changedName");
const currentHashEl = document.getElementById("currentHash");
const changedHashEl = document.getElementById("changedHash");
const currentTrackerEl = document.getElementById("currentTracker");
const changedTrackerEl = document.getElementById("changedTracker");
const currentSourceEl = document.getElementById("currentSource");
const changedSourceEl = document.getElementById("changedSource");
const currentCreatorEl = document.getElementById("currentCreator");
const changedCreatorEl = document.getElementById("changedCreator");
const removedKeysEl = document.getElementById("removedKeys");
const fileTreeWrapEl = document.getElementById("fileTreeWrap");
const fileTreeEl = document.getElementById("fileTree");

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder("utf-8", { fatal: false });
let loadedTorrent = null;
let loadedAnalysis = null;

function clearSelectedTorrent() {
  torrentInput.value = "";
  loadedTorrent = null;
  loadedAnalysis = null;
  detailsEl.hidden = true;
}

function restorePasskey() {
  try {
    const stored = localStorage.getItem(PASSKEY_STORAGE_KEY);
    if (stored) {
      passkeyInput.value = stored;
    }
  } catch (_error) {
    // Ignore storage access failures.
  }
}

function persistPasskey(value) {
  try {
    if (value) {
      localStorage.setItem(PASSKEY_STORAGE_KEY, value);
    } else {
      localStorage.removeItem(PASSKEY_STORAGE_KEY);
    }
  } catch (_error) {
    // Ignore storage access failures.
  }
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function bytesToString(bytes) {
  return textDecoder.decode(bytes);
}

function stringToBytes(text) {
  return textEncoder.encode(text);
}

function bytesForDisplay(bytes) {
  if (!(bytes instanceof Uint8Array)) {
    return "-";
  }
  const decoded = bytesToString(bytes).trim();
  return decoded || "(empty)";
}

function getChangedTracker(passkey) {
  if (!passkey) {
    return `${TRACKER_BASE}{passkey}`;
  }
  return `${TRACKER_BASE}${encodeURIComponent(passkey)}`;
}

function normalizeTitle(name) {
  return name
    .replace(/\.[^./\\]{1,5}$/u, "")
    .replace(/[\\/:*?"<>|]+/gu, " ")
    .replace(/[\s_]+/gu, ".")
    .replace(/\.+/gu, ".")
    .replace(/^\.+|\.+$/gu, "");
}

function getTorrentName(info) {
  if (!info || typeof info !== "object" || Array.isArray(info)) {
    return "unknown";
  }
  return bytesForDisplay(info.name);
}

function buildOutputName(info, fallbackInputName) {
  const currentName = getTorrentName(info);
  const fromInfo = normalizeTitle(currentName);
  if (fromInfo) {
    return `[la-cale]${fromInfo}.torrent`;
  }
  const fallbackBase = fallbackInputName.toLowerCase().endsWith(".torrent")
    ? fallbackInputName.slice(0, -8)
    : fallbackInputName;
  return `[la-cale]${normalizeTitle(fallbackBase) || "download"}.torrent`;
}

function asBigInt(value) {
  if (typeof value === "bigint") {
    return value;
  }
  if (typeof value === "number" && Number.isInteger(value)) {
    return BigInt(value);
  }
  return null;
}

function formatBytes(value) {
  const size = asBigInt(value);
  if (size === null) {
    return "? B";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let unitIndex = 0;
  let scaled = Number(size);
  if (!Number.isFinite(scaled)) {
    return `${size.toString()} B`;
  }
  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  const display = scaled >= 10 || unitIndex === 0 ? scaled.toFixed(0) : scaled.toFixed(1);
  return `${display} ${units[unitIndex]}`;
}

function sha1HexFallback(bytes) {
  const messageLengthBits = bytes.length * 8;
  const withOne = new Uint8Array(bytes.length + 1);
  withOne.set(bytes, 0);
  withOne[bytes.length] = 0x80;

  const zeroPadLength = (64 - ((withOne.length + 8) % 64)) % 64;
  const totalLength = withOne.length + zeroPadLength + 8;
  const padded = new Uint8Array(totalLength);
  padded.set(withOne, 0);

  const highBits = Math.floor(messageLengthBits / 0x100000000);
  const lowBits = messageLengthBits >>> 0;
  padded[totalLength - 8] = (highBits >>> 24) & 0xff;
  padded[totalLength - 7] = (highBits >>> 16) & 0xff;
  padded[totalLength - 6] = (highBits >>> 8) & 0xff;
  padded[totalLength - 5] = highBits & 0xff;
  padded[totalLength - 4] = (lowBits >>> 24) & 0xff;
  padded[totalLength - 3] = (lowBits >>> 16) & 0xff;
  padded[totalLength - 2] = (lowBits >>> 8) & 0xff;
  padded[totalLength - 1] = lowBits & 0xff;

  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;

  const w = new Uint32Array(80);
  for (let chunkStart = 0; chunkStart < padded.length; chunkStart += 64) {
    for (let i = 0; i < 16; i += 1) {
      const j = chunkStart + i * 4;
      w[i] = (padded[j] << 24) | (padded[j + 1] << 16) | (padded[j + 2] << 8) | padded[j + 3];
    }

    for (let i = 16; i < 80; i += 1) {
      const n = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = ((n << 1) | (n >>> 31)) >>> 0;
    }

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;

    for (let i = 0; i < 80; i += 1) {
      let f;
      let k;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }

      const temp = ((((a << 5) | (a >>> 27)) + f + e + k + w[i]) & 0xffffffff) >>> 0;
      e = d;
      d = c;
      c = ((b << 30) | (b >>> 2)) >>> 0;
      b = a;
      a = temp;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }

  return [h0, h1, h2, h3, h4]
    .map((value) => value.toString(16).padStart(8, "0"))
    .join("");
}

function sha1Hex(bytes) {
  if (globalThis.crypto && globalThis.crypto.subtle && typeof globalThis.crypto.subtle.digest === "function") {
    return globalThis.crypto.subtle.digest("SHA-1", bytes).then((digest) => {
      const arr = new Uint8Array(digest);
      return Array.from(arr, (item) => item.toString(16).padStart(2, "0")).join("");
    });
  }

  return Promise.resolve(sha1HexFallback(bytes));
}

function buildFileTree(info) {
  const root = { dirs: new Map(), files: [] };

  function addFile(pathParts, lengthValue) {
    let node = root;
    for (let i = 0; i < pathParts.length - 1; i += 1) {
      const part = pathParts[i];
      if (!node.dirs.has(part)) {
        node.dirs.set(part, { dirs: new Map(), files: [] });
      }
      node = node.dirs.get(part);
    }
    node.files.push({ name: pathParts[pathParts.length - 1], length: lengthValue });
  }

  if (Array.isArray(info.files)) {
    for (const file of info.files) {
      const rawPath = Array.isArray(file.path) ? file.path : Array.isArray(file["path.utf-8"]) ? file["path.utf-8"] : [];
      const parts = rawPath.map((part) => bytesForDisplay(part));
      if (parts.length > 0) {
        addFile(parts, file.length);
      }
    }
  } else {
    addFile([getTorrentName(info)], info.length);
  }

  return root;
}

function renderTreeNode(node) {
  const ul = document.createElement("ul");
  ul.className = "tree-list";

  const dirNames = Array.from(node.dirs.keys()).sort((a, b) => a.localeCompare(b));
  for (const dirName of dirNames) {
    const li = document.createElement("li");
    li.textContent = `${dirName}/`;
    li.appendChild(renderTreeNode(node.dirs.get(dirName)));
    ul.appendChild(li);
  }

  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
  for (const file of files) {
    const li = document.createElement("li");
    li.textContent = `${file.name} (${formatBytes(file.length)})`;
    ul.appendChild(li);
  }

  return ul;
}

async function analyzeTorrent(torrentDict, inputFileName) {
  const info = torrentDict.info;
  if (!info || typeof info !== "object" || Array.isArray(info)) {
    throw new Error("Torrent must contain an info dictionary");
  }
  const currentHash = await sha1Hex(encodeBencode(info));
  const changedInfo = { ...info, source: stringToBytes(SOURCE_VALUE) };
  const changedHash = await sha1Hex(encodeBencode(changedInfo));
  const removedKeys = Object.keys(torrentDict).filter((key) => key !== "announce" && key !== "info");

  return {
    name: getTorrentName(info),
    outputName: buildOutputName(info, inputFileName),
    currentHash,
    changedHash,
    currentTracker: bytesForDisplay(torrentDict.announce),
    currentSource: bytesForDisplay(info.source),
    currentCreator: bytesForDisplay(torrentDict["created by"] || torrentDict.creator),
    removedKeys,
    fileTree: buildFileTree(info)
  };
}

function renderDetails() {
  if (!loadedTorrent || !loadedAnalysis) {
    detailsEl.hidden = true;
    return;
  }

  currentNameEl.textContent = loadedAnalysis.name;
  changedNameEl.textContent = loadedAnalysis.outputName;
  currentHashEl.textContent = loadedAnalysis.currentHash;
  changedHashEl.textContent = loadedAnalysis.changedHash;
  currentTrackerEl.textContent = loadedAnalysis.currentTracker;
  changedTrackerEl.textContent = getChangedTracker(passkeyInput.value.trim());
  currentSourceEl.textContent = loadedAnalysis.currentSource;
  changedSourceEl.textContent = SOURCE_VALUE;

  currentCreatorEl.textContent = loadedAnalysis.currentCreator;
  changedCreatorEl.textContent = "removed";

  removedKeysEl.textContent = loadedAnalysis.removedKeys.length > 0
    ? loadedAnalysis.removedKeys.join(", ")
    : "(none)";

  fileTreeEl.innerHTML = "";
  fileTreeEl.appendChild(renderTreeNode(loadedAnalysis.fileTree));
  fileTreeWrapEl.hidden = false;

  detailsEl.hidden = false;
}

function decodeBencode(inputBytes) {
  let offset = 0;

  function readNumberUntil(delimiter) {
    const start = offset;
    while (offset < inputBytes.length && inputBytes[offset] !== delimiter) {
      offset += 1;
    }
    if (offset >= inputBytes.length) {
      throw new Error("Invalid bencode: unterminated number");
    }
    const raw = bytesToString(inputBytes.slice(start, offset));
    offset += 1;
    return raw;
  }

  function parse() {
    if (offset >= inputBytes.length) {
      throw new Error("Invalid bencode: unexpected end of input");
    }

    const token = inputBytes[offset];

    if (token === 105) {
      offset += 1;
      const integerRaw = readNumberUntil(101);
      if (!/^-?\d+$/.test(integerRaw)) {
        throw new Error("Invalid bencode integer");
      }
      return BigInt(integerRaw);
    }

    if (token === 108) {
      offset += 1;
      const list = [];
      while (offset < inputBytes.length && inputBytes[offset] !== 101) {
        list.push(parse());
      }
      if (offset >= inputBytes.length) {
        throw new Error("Invalid bencode: unterminated list");
      }
      offset += 1;
      return list;
    }

    if (token === 100) {
      offset += 1;
      const dict = {};
      while (offset < inputBytes.length && inputBytes[offset] !== 101) {
        const keyBytes = parse();
        if (!(keyBytes instanceof Uint8Array)) {
          throw new Error("Invalid bencode dictionary key");
        }
        const key = bytesToString(keyBytes);
        dict[key] = parse();
      }
      if (offset >= inputBytes.length) {
        throw new Error("Invalid bencode: unterminated dictionary");
      }
      offset += 1;
      return dict;
    }

    if (token >= 48 && token <= 57) {
      const lengthRaw = readNumberUntil(58);
      if (!/^\d+$/.test(lengthRaw)) {
        throw new Error("Invalid bencode string length");
      }
      const length = Number(lengthRaw);
      if (!Number.isSafeInteger(length) || length < 0) {
        throw new Error("Invalid bencode string length value");
      }
      const start = offset;
      const end = start + length;
      if (end > inputBytes.length) {
        throw new Error("Invalid bencode: string exceeds input size");
      }
      offset = end;
      return inputBytes.slice(start, end);
    }

    throw new Error("Invalid bencode token");
  }

  const value = parse();
  if (offset !== inputBytes.length) {
    throw new Error("Invalid bencode: trailing data");
  }
  return value;
}

function encodeBencode(value) {
  const chunks = [];

  function pushBytes(bytes) {
    chunks.push(bytes);
  }

  function encode(v) {
    if (typeof v === "bigint") {
      pushBytes(stringToBytes(`i${v.toString()}e`));
      return;
    }

    if (typeof v === "number") {
      if (!Number.isInteger(v)) {
        throw new Error("Only integer numbers are supported in bencode");
      }
      pushBytes(stringToBytes(`i${v}e`));
      return;
    }

    if (v instanceof Uint8Array) {
      pushBytes(stringToBytes(`${v.length}:`));
      pushBytes(v);
      return;
    }

    if (typeof v === "string") {
      const bytes = stringToBytes(v);
      pushBytes(stringToBytes(`${bytes.length}:`));
      pushBytes(bytes);
      return;
    }

    if (Array.isArray(v)) {
      pushBytes(stringToBytes("l"));
      for (const item of v) {
        encode(item);
      }
      pushBytes(stringToBytes("e"));
      return;
    }

    if (v && typeof v === "object") {
      pushBytes(stringToBytes("d"));
      const keys = Object.keys(v).sort((a, b) => {
        const aa = stringToBytes(a);
        const bb = stringToBytes(b);
        const len = Math.min(aa.length, bb.length);
        for (let i = 0; i < len; i += 1) {
          if (aa[i] !== bb[i]) {
            return aa[i] - bb[i];
          }
        }
        return aa.length - bb.length;
      });
      for (const key of keys) {
        encode(stringToBytes(key));
        encode(v[key]);
      }
      pushBytes(stringToBytes("e"));
      return;
    }

    throw new Error("Unsupported value in bencode encoder");
  }

  encode(value);

  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const output = new Uint8Array(totalLength);
  let position = 0;
  for (const chunk of chunks) {
    output.set(chunk, position);
    position += chunk.length;
  }
  return output;
}

function cleanTorrentMeta(torrentDict, passkey) {
  if (!torrentDict || typeof torrentDict !== "object" || Array.isArray(torrentDict)) {
    throw new Error("Torrent root must be a dictionary");
  }

  const info = torrentDict.info;
  if (!info || typeof info !== "object" || Array.isArray(info)) {
    throw new Error("Torrent must contain an info dictionary");
  }

  const cleaned = {
    announce: stringToBytes(`${TRACKER_BASE}${encodeURIComponent(passkey)}`),
    info: { ...info, source: stringToBytes(SOURCE_VALUE) }
  };

  return cleaned;
}

function triggerDownload(fileName, bytes) {
  const blob = new Blob([bytes], { type: "application/x-bittorrent" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

processBtn.addEventListener("click", async () => {
  try {
    const passkey = passkeyInput.value.trim();
    const file = torrentInput.files?.[0];

    if (!passkey) {
      setStatus("Please provide your passkey.", true);
      return;
    }
    if (!file) {
      setStatus("Please choose a .torrent file.", true);
      return;
    }

    setStatus("Processing torrent...");

    const input = new Uint8Array(await file.arrayBuffer());
    const decoded = decodeBencode(input);
    loadedTorrent = decoded;
    loadedAnalysis = await analyzeTorrent(decoded, file.name);
    renderDetails();
    const cleaned = cleanTorrentMeta(decoded, passkey);
    const encoded = encodeBencode(cleaned);
    const fileName = loadedAnalysis ? loadedAnalysis.outputName : buildOutputName(decoded.info, file.name);

    triggerDownload(fileName, encoded);
    setStatus("Done. Your cleaned torrent has been downloaded.");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setStatus(`Error: ${message}`, true);
  }
});

torrentInput.addEventListener("change", async () => {
  const file = torrentInput.files?.[0];
  if (!file) {
    loadedTorrent = null;
    loadedAnalysis = null;
    detailsEl.hidden = true;
    return;
  }

  try {
    const input = new Uint8Array(await file.arrayBuffer());
    loadedTorrent = decodeBencode(input);
    loadedAnalysis = await analyzeTorrent(loadedTorrent, file.name);
    renderDetails();
    setStatus("Torrent parsed. Review details below before download.");
  } catch (error) {
    loadedTorrent = null;
    loadedAnalysis = null;
    detailsEl.hidden = true;
    const message = error instanceof Error ? error.message : String(error);
    setStatus(`Error: ${message}`, true);
  }
});

passkeyInput.addEventListener("input", () => {
  persistPasskey(passkeyInput.value.trim());
  if (loadedTorrent) {
    renderDetails();
  }
});

restorePasskey();
clearSelectedTorrent();
window.addEventListener("pageshow", () => {
  clearSelectedTorrent();
});
