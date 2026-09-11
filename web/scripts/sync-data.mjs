import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));
const dataDirectory = path.join(repositoryRoot, "data", "processed");
const destinationDirectory = path.join(repositoryRoot, "web", "public", "data");

const artifacts = [
  {
    name: "candidates.geojson",
    source: path.join(dataDirectory, "delivery", "candidates.geojson"),
    required: true,
  },
  {
    name: "candidates.metadata.json",
    source: path.join(dataDirectory, "delivery", "candidates.metadata.json"),
    required: true,
  },
  {
    name: "candidate_shortlists.json",
    source: path.join(dataDirectory, "delivery", "candidate_shortlists.json"),
    required: true,
  },
  {
    name: "candidate_weight_index.json",
    source: path.join(dataDirectory, "delivery", "candidate_weight_index.json"),
    required: true,
  },
  {
    name: "presets.json",
    source: path.join(dataDirectory, "prioritization", "presets.json"),
    required: true,
  },
  {
    name: "protected_areas.geojson",
    source: path.join(dataDirectory, "delivery", "protected_areas.geojson"),
    required: true,
  },
  {
    name: "wetland_inland_water.geojson",
    source: path.join(dataDirectory, "delivery", "wetland_inland_water.geojson"),
    required: true,
  },
];

const generationHint =
  "Generate canonical assets from the repository root with: python -m restoration_prioritizer.prioritization_model and python -m restoration_prioritizer.web_delivery";
const maxPagesAssetBytes = 25 * 1024 * 1024;

async function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function ensureSourceExists(artifact) {
  try {
    await stat(artifact.source);
    return true;
  } catch (error) {
    if (error.code === "ENOENT" && !artifact.required) return false;
    throw new Error(`Missing required delivery artifact: ${artifact.source}\n${generationHint}`, {
      cause: error,
    });
  }
}

async function readMetadata() {
  const metadataPath = artifacts[1].source;
  try {
    return JSON.parse(await readFile(metadataPath, "utf8"));
  } catch (error) {
    throw new Error(`Cannot read delivery metadata: ${metadataPath}\n${generationHint}`, {
      cause: error,
    });
  }
}

await mkdir(destinationDirectory, { recursive: true });
const metadata = await readMetadata();
const candidateArtifact = artifacts[0];
await ensureSourceExists(candidateArtifact);
const candidateBytes = await readFile(candidateArtifact.source);
const sourceHash = await sha256(candidateBytes);

if (candidateBytes.byteLength >= maxPagesAssetBytes) {
  throw new Error(
    `Candidate GeoJSON is ${candidateBytes.byteLength} bytes; Cloudflare Pages static assets must remain below ${maxPagesAssetBytes} bytes (25 MiB). Reduce the artifact only through a delivery-contract review.`,
  );
}

if (metadata.file_size_bytes !== candidateBytes.byteLength) {
  throw new Error(
    `Candidate GeoJSON size mismatch: metadata=${metadata.file_size_bytes}, source=${candidateBytes.byteLength}.\n${generationHint}`,
  );
}
if (metadata.sha256 && metadata.sha256 !== sourceHash) {
  throw new Error(
    `Candidate GeoJSON SHA-256 mismatch: metadata=${metadata.sha256}, source=${sourceHash}.\n${generationHint}`,
  );
}

const copied = [];
for (const artifact of artifacts) {
  if (!(await ensureSourceExists(artifact))) continue;
  const sourceStats = await stat(artifact.source);
  if (sourceStats.size >= maxPagesAssetBytes) {
    throw new Error(
      `${artifact.name} is ${sourceStats.size} bytes; Cloudflare Pages static assets must remain below ${maxPagesAssetBytes} bytes (25 MiB).`,
    );
  }
  const destination = path.join(destinationDirectory, artifact.name);
  await copyFile(artifact.source, destination);
  const destinationStats = await stat(destination);
  if (sourceStats.size !== destinationStats.size) {
    throw new Error(`Copied size mismatch for ${artifact.name}: ${artifact.source} -> ${destination}`);
  }
  copied.push({ name: artifact.name, bytes: destinationStats.size });
}

console.log(
  JSON.stringify(
    {
      status: "ok",
      copied,
      candidatesSha256: sourceHash,
      metadataFeatureCount: metadata.feature_count,
      metadataFileSizeBytes: metadata.file_size_bytes,
    },
    null,
    2,
  ),
);
