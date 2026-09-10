import { useEffect, useState } from "react";
import { CandidateMap } from "./map/CandidateMap";
import { loadDeliveryMetadata } from "./data/metadata";
import type { DeliveryMetadata } from "./data/types";
import "./styles.css";

export default function App() {
  const [metadata, setMetadata] = useState<DeliveryMetadata>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    loadDeliveryMetadata().then(setMetadata).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Delivery metadata could not be loaded");
    });
  }, []);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1>Landscape Restoration Prioritizer</h1>
          <p>Skåne, Sweden</p>
        </div>
        <span className="technical-label">Frontend foundation</span>
      </header>
      <section className="map-stage">
        {error ? (
          <div className="loading-error" role="alert">
            Delivery data error: {error}
          </div>
        ) : metadata ? (
          <CandidateMap metadata={metadata} />
        ) : (
          <div className="loading-message">Loading delivery metadata…</div>
        )}
      </section>
    </main>
  );
}
