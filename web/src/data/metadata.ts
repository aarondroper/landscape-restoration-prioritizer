import type { DeliveryMetadata } from "./types";

export const METADATA_URL = "/data/candidates.metadata.json";

export async function loadDeliveryMetadata(): Promise<DeliveryMetadata> {
  const response = await fetch(METADATA_URL);
  if (!response.ok) {
    throw new Error(`Delivery metadata request failed (${response.status})`);
  }
  const metadata = (await response.json()) as DeliveryMetadata;
  if (
    !metadata.bbox_epsg_4326 ||
    !Number.isFinite(metadata.feature_count) ||
    metadata.feature_count <= 0
  ) {
    throw new Error("Delivery metadata is missing a valid bbox or feature count");
  }
  return metadata;
}
