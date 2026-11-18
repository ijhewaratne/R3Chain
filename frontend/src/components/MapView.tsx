import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { UIStrings } from "../i18n/strings";
import type { MapBundleData } from "../types/api";

interface MapViewProps {
  t: UIStrings;
  mapBundle: MapBundleData | null;
}

const LAYER_ORDER = [
  "study_area",
  "targets",
  "restrictions",
  "corridors",
  "barriers",
  "demand",
  "sites",
  "connections",
  "routes",
  "candidates",
] as const;

const LAYER_COLORS: Record<string, string> = {
  study_area: "#475569",
  targets: "#0284c7",
  restrictions: "#dc2626",
  corridors: "#64748b",
  barriers: "#b45309",
  demand: "#7c3aed",
  sites: "#0b4f8a",
  connections: "#0f766e",
  routes: "#2563eb",
  candidates: "#15803d",
};

function projectRingsToSvg(
  coords: number[][],
  bbox: [number, number, number, number],
  width: number,
  height: number
): string {
  const [minX, minY, maxX, maxY] = bbox;
  const spanX = Math.max(maxX - minX, 1e-6);
  const spanY = Math.max(maxY - minY, 1e-6);
  const pad = 18;
  return coords
    .map(([x, y]) => {
      const px = pad + ((x - minX) / spanX) * (width - pad * 2);
      const py = height - pad - ((y - minY) / spanY) * (height - pad * 2);
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    })
    .join(" ");
}

export function MapView({ t, mapBundle }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<maplibregl.Map | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [useFallbackSvg, setUseFallbackSvg] = useState(false);
  const [selectedFeature, setSelectedFeature] = useState<Record<string, unknown> | null>(null);
  const [visibleLayers, setVisibleLayers] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    for (const key of LAYER_ORDER) initial[key] = true;
    return initial;
  });

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !mapBundle) return;

    const hasWebGl = (() => {
      if (typeof window === "undefined" || !("WebGLRenderingContext" in window)) {
        return false;
      }
      const probe = document.createElement("canvas");
      return Boolean(probe.getContext("webgl2") || probe.getContext("webgl"));
    })();
    if (!hasWebGl) {
      setUseFallbackSvg(true);
      return;
    }

    let cancelled = false;
    try {
      const center: [number, number] = mapBundle.bbox
        ? [
            (mapBundle.bbox[0] + mapBundle.bbox[2]) / 2,
            (mapBundle.bbox[1] + mapBundle.bbox[3]) / 2,
          ]
        : [9.03, 51.465];

      const map = new maplibregl.Map({
        container: el,
        style: {
          version: 8,
          sources: {},
          layers: [
            {
              id: "r3-canvas-bg",
              type: "background",
              paint: { "background-color": "#f1f5f9" },
            },
          ],
        },
        center,
        zoom: 12,
        attributionControl: false,
      });
      map.addControl(new maplibregl.NavigationControl(), "top-right");
      map.on("error", () => {
        if (!cancelled) setUseFallbackSvg(true);
      });
      map.on("load", () => {
        if (cancelled) return;
        for (const layerKey of LAYER_ORDER) {
          const fc = mapBundle.layers[layerKey];
          if (!fc) continue;
          const srcId = `src-${layerKey}`;
          map.addSource(srcId, { type: "geojson", data: fc as unknown as GeoJSON.GeoJSON });
          const color = LAYER_COLORS[layerKey] || "#334155";
          const fillId = `fill-${layerKey}`;
          const lineId = `line-${layerKey}`;
          const ptId = `pt-${layerKey}`;
          map.addLayer({
            id: fillId,
            type: "fill",
            source: srcId,
            filter: ["==", ["geometry-type"], "Polygon"],
            paint: {
              "fill-color": color,
              "fill-opacity": layerKey === "study_area" ? 0.06 : 0.28,
            },
          });
          map.addLayer({
            id: lineId,
            type: "line",
            source: srcId,
            filter: ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString", "Polygon"]]],
            paint: {
              "line-color": color,
              "line-width": layerKey === "candidates" || layerKey === "routes" ? 3.5 : 2,
              ...(layerKey === "barriers" || layerKey === "restrictions"
                ? { "line-dasharray": [2, 2] }
                : {}),
            },
          });
          map.addLayer({
            id: ptId,
            type: "circle",
            source: srcId,
            filter: ["==", ["geometry-type"], "Point"],
            paint: {
              "circle-radius": 6,
              "circle-color": color,
              "circle-stroke-width": 2,
              "circle-stroke-color": "#ffffff",
            },
          });

          for (const lid of [fillId, lineId, ptId]) {
            map.on("click", lid, (ev) => {
              const feat = ev.features?.[0];
              if (feat?.properties) {
                setSelectedFeature({ layer: layerKey, ...feat.properties });
              }
            });
          }
        }
        if (mapBundle.bbox) {
          map.fitBounds(
            [
              [mapBundle.bbox[0], mapBundle.bbox[1]],
              [mapBundle.bbox[2], mapBundle.bbox[3]],
            ],
            { padding: 28, duration: 0 }
          );
        }
        setMapReady(true);
      });
      mapInstanceRef.current = map;
      return () => {
        cancelled = true;
        map.remove();
        mapInstanceRef.current = null;
        setMapReady(false);
      };
    } catch {
      setUseFallbackSvg(true);
    }
  }, [mapBundle]);

  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map || !mapReady) return;
    for (const key of LAYER_ORDER) {
      const vis = visibleLayers[key] ? "visible" : "none";
      for (const prefix of ["fill-", "line-", "pt-"]) {
        const lid = `${prefix}${key}`;
        if (map.getLayer(lid)) {
          map.setLayoutProperty(lid, "visibility", vis);
        }
      }
    }
  }, [visibleLayers, mapReady]);

  const fallbackBbox = useMemo<[number, number, number, number]>(() => {
    return mapBundle?.bbox ?? [9.0, 51.45, 9.07, 51.49];
  }, [mapBundle]);

  const toggleLayer = (key: string) => {
    setVisibleLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  if (!mapBundle) {
    return <p>{t.common.loading}</p>;
  }

  return (
    <section aria-labelledby="map-section-heading">
      <h2 id="map-section-heading">{t.map.heading}</h2>

      {mapBundle.hidden_spatial_layers_count > 0 ? (
        <div className="alert alert--warn" role="status">
          <span aria-hidden="true">⚠ </span>
          {mapBundle.hidden_spatial_layers_count} {t.map.hiddenLayersNote}
        </div>
      ) : null}

      <fieldset style={{ marginBlockEnd: "0.75rem" }}>
        <legend>{t.map.layersLegend}</legend>
        <div className="checkbox-row">
          {LAYER_ORDER.map((key) => {
            const count = mapBundle.layers[key]?.features?.length ?? 0;
            return (
              <label key={key} className="checkbox-item" htmlFor={`layer-toggle-${key}`}>
                <input
                  id={`layer-toggle-${key}`}
                  type="checkbox"
                  checked={Boolean(visibleLayers[key])}
                  onChange={() => toggleLayer(key)}
                />
                <span>
                  {t.map.layerNames[key] || key} ({count})
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="map-layout">
        <div className="map-canvas" aria-label={t.map.heading}>
          {!useFallbackSvg ? (
            <div ref={containerRef} style={{ inlineSize: "100%", blockSize: "100%" }} />
          ) : (
            <svg
              role="img"
              aria-label={t.map.heading}
              viewBox="0 0 640 420"
              style={{ inlineSize: "100%", blockSize: "100%", display: "block" }}
            >
              <title>{t.map.heading}</title>
              {LAYER_ORDER.filter((k) => visibleLayers[k]).map((key) =>
                (mapBundle.layers[key]?.features || []).map((feat, idx) => {
                  const geom = feat.geometry;
                  const gType = geom.type as string;
                  const color = LAYER_COLORS[key] || "#334155";
                  if (gType === "Polygon") {
                    const ring = (geom.coordinates as number[][][])[0] || [];
                    return (
                      <polygon
                        key={`${key}-${idx}`}
                        points={projectRingsToSvg(ring, fallbackBbox, 640, 420)}
                        fill={color}
                        fillOpacity={key === "study_area" ? 0.08 : 0.28}
                        stroke={color}
                        strokeWidth={2}
                        onClick={() => setSelectedFeature({ layer: key, ...feat.properties })}
                      />
                    );
                  }
                  if (gType === "LineString") {
                    const line = (geom.coordinates as number[][]) || [];
                    return (
                      <polyline
                        key={`${key}-${idx}`}
                        points={projectRingsToSvg(line, fallbackBbox, 640, 420)}
                        fill="none"
                        stroke={color}
                        strokeWidth={3}
                        strokeDasharray={key === "barriers" ? "6 4" : undefined}
                        onClick={() => setSelectedFeature({ layer: key, ...feat.properties })}
                      />
                    );
                  }
                  if (gType === "Point") {
                    const [x, y] = geom.coordinates as number[];
                    const pt = projectRingsToSvg([[x, y]], fallbackBbox, 640, 420);
                    const [cx, cy] = pt.split(",").map(Number);
                    return (
                      <circle
                        key={`${key}-${idx}`}
                        cx={cx}
                        cy={cy}
                        r={6}
                        fill={color}
                        stroke="#ffffff"
                        strokeWidth={2}
                        onClick={() => setSelectedFeature({ layer: key, ...feat.properties })}
                      />
                    );
                  }
                  return null;
                })
              )}
            </svg>
          )}
        </div>

        <aside className="panel-surface" aria-labelledby="map-inspector-heading">
          <h3 id="map-inspector-heading">{t.map.inspectorHeading}</h3>
          {selectedFeature ? (
            <dl>
              {Object.entries(selectedFeature).map(([k, v]) => (
                <div key={k} style={{ marginBlockEnd: "0.35rem" }}>
                  <dt style={{ fontWeight: 700, fontSize: "0.85rem" }}>{k}</dt>
                  <dd style={{ marginInlineStart: 0 }}>{String(v ?? "—")}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>{t.map.inspectorEmpty}</p>
          )}
        </aside>
      </div>

      <h3 style={{ marginBlockStart: "1rem" }}>{t.map.textTableCaption}</h3>
      <div
        tabIndex={0}
        role="region"
        aria-labelledby="map-summary-caption"
        className="table-wrapper responsive-stacked"
      >
        <table>
          <caption id="map-summary-caption">{t.map.textTableCaption}</caption>
          <thead>
            <tr>
              <th scope="col">{t.map.colKind}</th>
              <th scope="col">{t.map.colId}</th>
              <th scope="col">{t.map.colLabel}</th>
              <th scope="col">{t.map.colStatus}</th>
              <th scope="col">{t.map.colDetails}</th>
            </tr>
          </thead>
          <tbody>
            {mapBundle.text_summary.map((row, idx) => {
              const bad = row.status.includes("fail") || row.status.includes("rejected");
              const good = row.status.includes("pass") || row.status.includes("eligible");
              const badgeCls = bad ? "badge--fail" : good ? "badge--pass" : "badge--warn";
              const icon = bad ? "✕" : good ? "✓" : "•";
              return (
                <tr key={`${row.kind}-${row.id}-${idx}`}>
                  <td data-label={t.map.colKind}>{row.kind}</td>
                  <th scope="row">
                    <button
                      type="button"
                      className="btn"
                      onClick={() => setSelectedFeature(row as unknown as Record<string, unknown>)}
                    >
                      {row.id}
                    </button>
                  </th>
                  <td data-label={t.map.colLabel}>{row.label}</td>
                  <td data-label={t.map.colStatus}>
                    <span className={`badge ${badgeCls}`}>
                      <span aria-hidden="true">{icon}</span>
                      <span>{row.status}</span>
                    </span>
                  </td>
                  <td data-label={t.map.colDetails}>{row.details}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
