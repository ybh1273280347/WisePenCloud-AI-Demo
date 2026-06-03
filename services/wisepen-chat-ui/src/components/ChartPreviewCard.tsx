import {useEffect, useMemo, useState, type MouseEvent} from "react";
import {apiBaseUrl} from "../api/client";
import {fetchFileBlob} from "../api/file";
import {navigateToProvenanceNoteBlock} from "../utils/provenanceNavigation";

type ChartSourceTarget = {
  resource_kind?: string;
  resource_id?: string;
  resource_version?: string | null;
  block_id?: string;
  row_index?: number;
  column_name?: string;
};

type ChartToolOutput = {
  type: "quick_chart_result" | "traceable_chart_result";
  chart_type: string;
  title: string;
  output_format: "png" | "svg";
  image_file_ref?: string;
  mock_preview_markdown?: string;
  source_mode: "session_input" | "note_block" | string;
  traceable: boolean;
  source_map?: Record<string, ChartSourceTarget>;
};

type ChartPreviewCardProps = {
  output: string;
};

export function ChartPreviewCard({output}: ChartPreviewCardProps) {
  const chart = useMemo(() => parseChartOutput(output), [output]);
  const previewUrl = chart ? resolvePreviewUrl(chart) : null;
  const [svgText, setSvgText] = useState<string | null>(null);
  const [svgError, setSvgError] = useState<string | null>(null);

  useEffect(() => {
    setSvgText(null);
    setSvgError(null);
    if (!chart || chart.output_format !== "svg" || !previewUrl) {
      return;
    }

    let cancelled = false;
    fetchFileBlob(previewUrl)
      .then((blob) => blob.text())
      .then((text) => {
        if (!cancelled) {
          setSvgText(text);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setSvgError(error instanceof Error ? error.message : String(error));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [chart, previewUrl]);

  if (!chart || !previewUrl) {
    return null;
  }

  const sourceMap = chart.source_map || {};
  const sourceEntries = Object.entries(chart.source_map || {});

  function openSource(elementId: string, target: ChartSourceTarget | undefined) {
    if (!target?.resource_id || !target.block_id) {
      return;
    }
    navigateToProvenanceNoteBlock({
      resourceKind: target.resource_kind,
      resourceId: target.resource_id,
      resourceVersion: target.resource_version,
      blockId: target.block_id,
      rowIndex: target.row_index,
      columnName: target.column_name,
      chartElementId: elementId,
    });
  }

  function handleSvgClick(event: MouseEvent<HTMLDivElement>) {
    const target = event.target instanceof Element
      ? event.target.closest("[data-chart-element-id]")
      : null;
    const elementId = target?.getAttribute("data-chart-element-id");
    if (!elementId) {
      return;
    }
    openSource(elementId, sourceMap[elementId]);
  }

  return (
    <section className="chart-preview-card">
      <div className="chart-preview-header">
        <div>
          <span className="chart-preview-kicker">
            {chart.traceable ? "Traceable chart" : "Session chart"}
          </span>
          <h3>{chart.title}</h3>
        </div>
        <a className="chart-preview-open" href={toAbsoluteUrl(previewUrl)} target="_blank" rel="noreferrer">
          打开原图
        </a>
      </div>

      <div className="chart-preview-canvas">
        {chart.output_format === "svg" && svgText ? (
          <div
            className="chart-preview-svg"
            onClick={handleSvgClick}
            dangerouslySetInnerHTML={{__html: svgText}}
          />
        ) : (
          <img src={toAbsoluteUrl(previewUrl)} alt={chart.title} />
        )}
      </div>

      {svgError ? <div className="chart-preview-error">{svgError}</div> : null}

      {chart.traceable ? (
        <div className="chart-source-map-panel">
          <div className="chart-source-map-title">source_map 跳转测试</div>
          {sourceEntries.length > 0 ? (
            <div className="chart-source-map-list">
              {sourceEntries.map(([elementId, target]) => (
                <button
                  key={elementId}
                  type="button"
                  className="chart-source-target"
                  onClick={() => openSource(elementId, target)}
                >
                  <strong>{elementId}</strong>
                  <span>
                    {target.resource_id || "unknown"} / {target.block_id || "unknown"}
                    {target.row_index == null ? "" : ` · row ${target.row_index}`}
                    {target.column_name ? ` · ${target.column_name}` : ""}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p>暂无 source_map。</p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function parseChartOutput(output: string): ChartToolOutput | null {
  try {
    const parsed = JSON.parse(output) as Partial<ChartToolOutput>;
    if (
      (parsed.type === "quick_chart_result" || parsed.type === "traceable_chart_result") &&
      typeof parsed.title === "string" &&
      (parsed.output_format === "png" || parsed.output_format === "svg")
    ) {
      return parsed as ChartToolOutput;
    }
  } catch {
    return null;
  }
  return null;
}

function resolvePreviewUrl(chart: ChartToolOutput): string | null {
  const markdownUrl = chart.mock_preview_markdown?.match(/!\[[^\]]*]\(([^)]+)\)/)?.[1];
  if (markdownUrl) {
    return markdownUrl;
  }
  if (chart.image_file_ref) {
    return `/api/document-export/download?ref=${encodeURIComponent(chart.image_file_ref)}`;
  }
  return null;
}

function toAbsoluteUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  return `${apiBaseUrl}${path}`;
}
