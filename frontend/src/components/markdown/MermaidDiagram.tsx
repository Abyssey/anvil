import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Icon } from "@astryxdesign/core/Icon";
import { IconButton } from "@astryxdesign/core/IconButton";
import { Layout, LayoutContent } from "@astryxdesign/core/Layout";
import { ScrollableArea } from "@astryxdesign/core/ScrollableArea";
import {
  SegmentedControl,
  SegmentedControlItem,
} from "@astryxdesign/core/SegmentedControl";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import {
  ArrowPathIcon,
  ArrowsPointingOutIcon,
  CheckIcon,
  Square2StackIcon,
} from "@heroicons/react/24/outline";
import type { MermaidRenderResult } from "../../utils/mermaidRenderer";

type MermaidRendererModule = typeof import("../../utils/mermaidRenderer");

let rendererModulePromise: Promise<MermaidRendererModule> | undefined;

function loadMermaidRenderer(): Promise<MermaidRendererModule> {
  rendererModulePromise ??= import("../../utils/mermaidRenderer");
  return rendererModulePromise;
}

type RenderState =
  | { readonly status: "loading" }
  | {
      readonly status: "ready";
      readonly result: Extract<MermaidRenderResult, { readonly ok: true }>;
      readonly module: MermaidRendererModule;
    }
  | {
      readonly status: "error";
      readonly error: string;
    };

interface StageCamera {
  zoomFactor: number;
  vbX: number;
  vbY: number;
  vbW: number;
  vbH: number;
  baseUnitsPerPx: number;
}

const STAGE_PADDING_PX = 24;
const MIN_FIT_UNITS_PER_PX = 0.8;
const MAX_NATURAL_ZOOM = 4;
const KEYBOARD_ZOOM_STEP = 1.25;
const KEYBOARD_PAN_STEP_PX = 56;
const DRAG_DEADZONE_PX = 3;

function formatVbNum(value: number): string {
  return String(Math.round(value * 100) / 100);
}

function clampAxis(rawPos: number, naturalSpan: number, viewSpan: number): number {
  if (viewSpan >= naturalSpan) {
    const minPos = naturalSpan - viewSpan;
    const maxPos = 0;
    return Math.min(maxPos, Math.max(minPos, rawPos));
  }
  const pad = Math.min(48, (naturalSpan - viewSpan) * 0.5);
  const minPos = -pad;
  const maxPos = naturalSpan - viewSpan + pad;
  return Math.min(maxPos, Math.max(minPos, rawPos));
}

function getStageBox(
  host: HTMLElement,
  svg: SVGSVGElement,
): {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
} {
  const rect = svg.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    return {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
  }
  const fallbackW =
    host.clientWidth > 0
      ? host.clientWidth
      : typeof window !== "undefined" && window.innerWidth > 0
        ? Math.min(Math.round(window.innerWidth * 0.96), 1520) - 32
        : 1200;
  const fallbackH =
    host.clientHeight > 0
      ? host.clientHeight
      : typeof window !== "undefined" && window.innerHeight > 0
        ? Math.round(window.innerHeight * 0.78)
        : 640;
  return {
    left: rect.left,
    top: rect.top,
    width: Math.max(240, fallbackW),
    height: Math.max(200, fallbackH),
  };
}

function computeBaseUnitsPerPx(
  naturalWidth: number,
  naturalHeight: number,
  boxWidth: number,
  boxHeight: number,
): number {
  const safeW = Math.max(160, boxWidth - STAGE_PADDING_PX * 2);
  const safeH = Math.max(140, boxHeight - STAGE_PADDING_PX * 2);
  return Math.max(
    MIN_FIT_UNITS_PER_PX,
    naturalWidth / safeW,
    naturalHeight / safeH,
  );
}

export interface MermaidDiagramProps {
  readonly code: string;
  readonly label?: string;
  readonly isFinal?: boolean;
}

export function MermaidDiagram({
  code,
  label,
  isFinal = true,
}: MermaidDiagramProps) {
  const [renderState, setRenderState] = useState<RenderState>({ status: "loading" });
  const [viewMode, setViewMode] = useState<"diagram" | "source">("diagram");
  const [expandedViewMode, setExpandedViewMode] = useState<"diagram" | "source">("diagram");
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isZoomed, setIsZoomed] = useState(false);

  const inlineHostRef = useRef<HTMLDivElement>(null);
  const dialogHostRef = useRef<HTMLDivElement>(null);
  const copyTimerRef = useRef<number | null>(null);
  const resetZoomFnRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!isFinal) {
      return;
    }

    let cancelled = false;
    setRenderState({ status: "loading" });

    void (async () => {
      try {
        const mod = await loadMermaidRenderer();
        const result = await mod.renderMermaidSafe(code);
        if (cancelled) {
          return;
        }
        if (result.ok) {
          setRenderState({ status: "ready", result, module: mod });
        } else {
          setRenderState({ status: "error", error: result.error });
        }
      } catch (err) {
        if (cancelled) {
          return;
        }
        setRenderState({
          status: "error",
          error:
            err instanceof Error
              ? err.message
              : "Failed to load Mermaid renderer.",
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code, isFinal]);

  useLayoutEffect(() => {
    const host = inlineHostRef.current;
    if (!host) {
      return;
    }
    if (renderState.status !== "ready" || viewMode !== "diagram") {
      host.replaceChildren();
      return;
    }
    const fragment = renderState.module.sanitizeMermaidSvgToFragment(
      renderState.result.svg,
      { maxWidthPx: renderState.result.width },
    );
    host.replaceChildren(fragment);
    return () => {
      host.replaceChildren();
    };
  }, [renderState, viewMode]);

  useLayoutEffect(() => {
    const host = dialogHostRef.current;
    if (!host) {
      return;
    }
    if (!isExpanded || renderState.status !== "ready" || expandedViewMode !== "diagram") {
      host.replaceChildren();
      resetZoomFnRef.current = null;
      return;
    }

    const { width: natW, height: natH, svg: rawSvg } = renderState.result;
    const fragment = renderState.module.sanitizeMermaidSvgToFragment(rawSvg, {
      stageFill: true,
    });
    host.replaceChildren(fragment);

    const svg = host.querySelector("svg");
    if (!svg || natW <= 0 || natH <= 0) {
      return () => {
        host.replaceChildren();
      };
    }

    host.style.overflow = "hidden";
    host.style.touchAction = "none";
    host.style.userSelect = "none";

    const camera: StageCamera = {
      zoomFactor: 1,
      vbX: 0,
      vbY: 0,
      vbW: natW,
      vbH: natH,
      baseUnitsPerPx: 1,
    };

    const syncSvgViewBox = () => {
      svg.setAttribute(
        "viewBox",
        `${formatVbNum(camera.vbX)} ${formatVbNum(camera.vbY)} ${formatVbNum(camera.vbW)} ${formatVbNum(camera.vbH)}`,
      );
    };

    const resetToFit = () => {
      const box = getStageBox(host, svg);
      const baseUnits = computeBaseUnitsPerPx(natW, natH, box.width, box.height);
      camera.zoomFactor = 1;
      camera.baseUnitsPerPx = baseUnits;
      camera.vbW = box.width * baseUnits;
      camera.vbH = box.height * baseUnits;
      camera.vbX = (natW - camera.vbW) / 2;
      camera.vbY = (natH - camera.vbH) / 2;
      syncSvgViewBox();
      svg.style.cursor = "zoom-in";
      setIsZoomed(false);
    };

    const zoomAt = (
      targetZoomFactor: number,
      anchorClientX?: number,
      anchorClientY?: number,
    ) => {
      const box = getStageBox(host, svg);
      const baseUnits = computeBaseUnitsPerPx(natW, natH, box.width, box.height);
      camera.baseUnitsPerPx = baseUnits;
      const maxZoomFactor = Math.max(
        MAX_NATURAL_ZOOM,
        baseUnits * MAX_NATURAL_ZOOM,
      );
      const nextZoom = Math.min(maxZoomFactor, Math.max(1, targetZoomFactor));

      if (nextZoom <= 1.01) {
        resetToFit();
        return;
      }

      const px =
        anchorClientX !== undefined
          ? anchorClientX - box.left
          : box.width / 2;
      const py =
        anchorClientY !== undefined
          ? anchorClientY - box.top
          : box.height / 2;

      const curUnitsPerPx = camera.vbW / box.width;
      const worldX = camera.vbX + px * curUnitsPerPx;
      const worldY = camera.vbY + py * curUnitsPerPx;

      const nextUnitsPerPx = baseUnits / nextZoom;
      const nextVbW = box.width * nextUnitsPerPx;
      const nextVbH = box.height * nextUnitsPerPx;

      camera.zoomFactor = nextZoom;
      camera.vbW = nextVbW;
      camera.vbH = nextVbH;
      camera.vbX = clampAxis(worldX - px * nextUnitsPerPx, natW, nextVbW);
      camera.vbY = clampAxis(worldY - py * nextUnitsPerPx, natH, nextVbH);
      syncSvgViewBox();
      svg.style.cursor = "grab";
      setIsZoomed(true);
    };

    const panByScreenDelta = (dxPx: number, dyPx: number) => {
      if (camera.zoomFactor <= 1.01) {
        return;
      }
      const box = getStageBox(host, svg);
      const curUnitsPerPx = camera.vbW / box.width;
      camera.vbX = clampAxis(
        camera.vbX - dxPx * curUnitsPerPx,
        natW,
        camera.vbW,
      );
      camera.vbY = clampAxis(
        camera.vbY - dyPx * curUnitsPerPx,
        natH,
        camera.vbH,
      );
      syncSvgViewBox();
    };

    resetToFit();
    resetZoomFnRef.current = resetToFit;

    const pointers = new Map<number, { x: number; y: number }>();
    let pinchPrevDist = 0;
    let dragDistance = 0;

    const getPinchMetrics = () => {
      const pts = [...pointers.values()];
      const p0 = pts[0];
      const p1 = pts[1];
      if (!p0 || !p1) {
        return null;
      }
      return {
        distance: Math.hypot(p1.x - p0.x, p1.y - p0.y),
        midX: (p0.x + p1.x) / 2,
        midY: (p0.y + p1.y) / 2,
      };
    };

    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rawDelta =
        event.deltaMode === 1
          ? event.deltaY * 32
          : event.deltaMode === 2
            ? event.deltaY * 240
            : event.deltaY;
      const clampedDelta = Math.max(-180, Math.min(180, rawDelta));
      if (clampedDelta === 0) {
        return;
      }
      const factor = 2 ** (-clampedDelta * 0.003);
      zoomAt(camera.zoomFactor * factor, event.clientX, event.clientY);
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse" && event.button !== 0) {
        return;
      }
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      dragDistance = 0;
      if (pointers.size === 2) {
        pinchPrevDist = getPinchMetrics()?.distance ?? 0;
      }
      if (pointers.size === 2 || camera.zoomFactor > 1.01) {
        host.setPointerCapture?.(event.pointerId);
        svg.style.cursor = "grabbing";
      }
    };

    const handlePointerMove = (event: PointerEvent) => {
      const prev = pointers.get(event.pointerId);
      if (!prev) {
        return;
      }
      if (pointers.size === 2) {
        const before = getPinchMetrics();
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        const after = getPinchMetrics();
        if (before && after && pinchPrevDist > 0 && after.distance > 0) {
          zoomAt(
            camera.zoomFactor * (after.distance / before.distance),
            after.midX,
            after.midY,
          );
          panByScreenDelta(after.midX - before.midX, after.midY - before.midY);
          pinchPrevDist = after.distance;
        }
        return;
      }

      const dx = event.clientX - prev.x;
      const dy = event.clientY - prev.y;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (camera.zoomFactor <= 1.01) {
        return;
      }
      dragDistance += Math.hypot(dx, dy);
      if (dragDistance >= DRAG_DEADZONE_PX) {
        svg.style.cursor = "grabbing";
        panByScreenDelta(dx, dy);
      }
    };

    const handlePointerEnd = (event: PointerEvent) => {
      if (!pointers.delete(event.pointerId)) {
        return;
      }
      host.releasePointerCapture?.(event.pointerId);
      pinchPrevDist = 0;
      if (pointers.size === 0) {
        svg.style.cursor = camera.zoomFactor > 1.01 ? "grab" : "zoom-in";
      }
    };

    const handleDoubleClick = (event: MouseEvent) => {
      event.preventDefault();
      if (camera.zoomFactor > 1.02) {
        resetToFit();
      } else {
        const readableZoom = Math.max(2.2, camera.baseUnitsPerPx * 1.15);
        zoomAt(readableZoom, event.clientX, event.clientY);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        zoomAt(camera.zoomFactor * KEYBOARD_ZOOM_STEP);
      } else if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        zoomAt(camera.zoomFactor / KEYBOARD_ZOOM_STEP);
      } else if (event.key === "0") {
        event.preventDefault();
        resetToFit();
      } else if (camera.zoomFactor > 1.01) {
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          panByScreenDelta(KEYBOARD_PAN_STEP_PX, 0);
        } else if (event.key === "ArrowRight") {
          event.preventDefault();
          panByScreenDelta(-KEYBOARD_PAN_STEP_PX, 0);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          panByScreenDelta(0, KEYBOARD_PAN_STEP_PX);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          panByScreenDelta(0, -KEYBOARD_PAN_STEP_PX);
        }
      }
    };

    const resizeObserver =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            if (camera.zoomFactor <= 1.01) {
              resetToFit();
            } else {
              const box = getStageBox(host, svg);
              camera.baseUnitsPerPx = computeBaseUnitsPerPx(
                natW,
                natH,
                box.width,
                box.height,
              );
              const unitsPerPx = camera.baseUnitsPerPx / camera.zoomFactor;
              camera.vbW = box.width * unitsPerPx;
              camera.vbH = box.height * unitsPerPx;
              camera.vbX = clampAxis(camera.vbX, natW, camera.vbW);
              camera.vbY = clampAxis(camera.vbY, natH, camera.vbH);
              syncSvgViewBox();
            }
          })
        : null;
    resizeObserver?.observe(host);

    host.addEventListener("wheel", handleWheel, { passive: false });
    host.addEventListener("pointerdown", handlePointerDown);
    host.addEventListener("pointermove", handlePointerMove);
    host.addEventListener("pointerup", handlePointerEnd);
    host.addEventListener("pointercancel", handlePointerEnd);
    host.addEventListener("dblclick", handleDoubleClick);
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      resizeObserver?.disconnect();
      host.removeEventListener("wheel", handleWheel);
      host.removeEventListener("pointerdown", handlePointerDown);
      host.removeEventListener("pointermove", handlePointerMove);
      host.removeEventListener("pointerup", handlePointerEnd);
      host.removeEventListener("pointercancel", handlePointerEnd);
      host.removeEventListener("dblclick", handleDoubleClick);
      window.removeEventListener("keydown", handleKeyDown);
      host.style.removeProperty("overflow");
      host.style.removeProperty("touch-action");
      host.style.removeProperty("user-select");
      host.replaceChildren();
      resetZoomFnRef.current = null;
    };
  }, [isExpanded, renderState, expandedViewMode]);

  const handleOpenExpand = useCallback(() => {
    setExpandedViewMode("diagram");
    setIsZoomed(false);
    setIsExpanded(true);
  }, []);

  const handleResetZoom = useCallback(() => {
    resetZoomFnRef.current?.();
  }, []);

  const sourceText =
    renderState.status === "ready" && renderState.result.repairedSource
      ? renderState.result.repairedSource
      : code;

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard?.writeText(sourceText);
      setCopied(true);
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setCopied(false);
      }, 1500);
    } catch {
      setCopied(false);
    }
  }, [sourceText]);

  if (!isFinal) {
    return (
      <CodeBlock
        code={code}
        language="plaintext"
        isWrapped
        width="100%"
      />
    );
  }

  const badgeLabel = label ? `Mermaid · ${label}` : "Mermaid";
  const dialogSubtitle =
    expandedViewMode === "diagram"
      ? renderState.status === "ready" && renderState.result.repaired
        ? "Auto-fixed · 휠로 확대/축소 · 드래그로 이동 · 더블클릭 초기화"
        : "휠로 확대/축소 · 드래그로 이동 · 더블클릭 초기화"
      : renderState.status === "ready" && renderState.result.repaired
        ? "Auto-fixed"
        : undefined;

  return (
    <Card padding={3} width="100%">
      <VStack gap={2}>
        <HStack gap={2} hAlign="between" vAlign="center" wrap="wrap">
          <HStack gap={1.5} vAlign="center" wrap="wrap">
            <StackItem size="static">
              <Badge variant="neutral" label={badgeLabel} />
            </StackItem>
            {renderState.status === "ready" && renderState.result.repaired && (
              <StackItem size="static">
                <Badge variant="info" label="Auto-fixed" />
              </StackItem>
            )}
            {renderState.status === "error" && (
              <StackItem size="static">
                <Badge variant="warning" label="Syntax fallback" />
              </StackItem>
            )}
          </HStack>

          <HStack gap={1} vAlign="center" wrap="wrap">
            {renderState.status === "ready" && (
              <StackItem size="static">
                <SegmentedControl
                  value={viewMode}
                  onChange={(val) =>
                    setViewMode(val === "source" ? "source" : "diagram")
                  }
                  label="Mermaid view mode"
                  size="sm"
                >
                  <SegmentedControlItem value="diagram" label="Diagram" />
                  <SegmentedControlItem value="source" label="Source" />
                </SegmentedControl>
              </StackItem>
            )}
            <StackItem size="static">
              <IconButton
                label={copied ? "Copied" : "Copy"}
                tooltip={copied ? "Copied" : "Copy Mermaid source"}
                variant="ghost"
                size="sm"
                icon={
                  <Icon
                    icon={copied ? CheckIcon : Square2StackIcon}
                    size="sm"
                  />
                }
                onClick={() => void handleCopy()}
              />
            </StackItem>
            {renderState.status === "ready" && (
              <StackItem size="static">
                <IconButton
                  label="Expand"
                  tooltip="Expand diagram"
                  variant="ghost"
                  size="sm"
                  icon={<Icon icon={ArrowsPointingOutIcon} size="sm" />}
                  onClick={handleOpenExpand}
                />
              </StackItem>
            )}
          </HStack>
        </HStack>

        {renderState.status === "loading" && (
          <HStack gap={2} vAlign="center" padding={2} role="status">
            <Spinner size="sm" shade="subtle" aria-hidden="true" />
            <Text type="supporting" color="secondary">
              Rendering Mermaid diagram...
            </Text>
          </HStack>
        )}

        {renderState.status === "ready" && viewMode === "diagram" && (
          <ScrollableArea
            ref={inlineHostRef}
            axis="inline"
            label={label ? `Mermaid diagram: ${label}` : "Mermaid diagram"}
            padding={2}
          />
        )}

        {renderState.status === "ready" && viewMode === "source" && (
          <CodeBlock
            code={sourceText}
            language="plaintext"
            isWrapped
            width="100%"
          />
        )}

        {renderState.status === "error" && (
          <VStack gap={2}>
            <Text type="supporting" color="secondary">
              Could not render Mermaid diagram: {renderState.error}
            </Text>
            <CodeBlock
              code={code}
              language="plaintext"
              isWrapped
              width="100%"
            />
          </VStack>
        )}
      </VStack>

      <Dialog
        isOpen={isExpanded}
        onOpenChange={setIsExpanded}
        purpose="info"
        width="min(96vw, 1520px)"
        maxHeight="92dvh"
      >
        <Layout
          height="auto"
          header={
            <DialogHeader
              title={label ? `Mermaid — ${label}` : "Mermaid Diagram"}
              subtitle={dialogSubtitle}
              endContent={
                <HStack gap={1} vAlign="center">
                  {expandedViewMode === "diagram" && isZoomed && (
                    <StackItem size="static">
                      <IconButton
                        label="Reset zoom"
                        tooltip="Reset to Fit (0 / double-click)"
                        variant="ghost"
                        size="sm"
                        icon={<Icon icon={ArrowPathIcon} size="sm" />}
                        onClick={handleResetZoom}
                      />
                    </StackItem>
                  )}
                  <StackItem size="static">
                    <SegmentedControl
                      value={expandedViewMode}
                      onChange={(val) =>
                        setExpandedViewMode(val === "source" ? "source" : "diagram")
                      }
                      label="Expanded Mermaid view mode"
                      size="sm"
                    >
                      <SegmentedControlItem value="diagram" label="Diagram" />
                      <SegmentedControlItem value="source" label="Source" />
                    </SegmentedControl>
                  </StackItem>
                  <StackItem size="static">
                    <IconButton
                      label={copied ? "Copied" : "Copy"}
                      tooltip={copied ? "Copied" : "Copy Mermaid source"}
                      variant="ghost"
                      size="sm"
                      icon={
                        <Icon
                          icon={copied ? CheckIcon : Square2StackIcon}
                          size="sm"
                        />
                      }
                      onClick={() => void handleCopy()}
                    />
                  </StackItem>
                </HStack>
              }
              onOpenChange={setIsExpanded}
            />
          }
          content={
            <LayoutContent padding={2}>
              {expandedViewMode === "diagram" ? (
                <ScrollableArea
                  ref={dialogHostRef}
                  axis="both"
                  overscroll="contain"
                  height="76dvh"
                  label="Expanded Mermaid diagram"
                  padding={0}
                />
              ) : (
                <CodeBlock
                  code={sourceText}
                  language="plaintext"
                  isWrapped
                  maxHeight="76dvh"
                  width="100%"
                />
              )}
            </LayoutContent>
          }
        />
      </Dialog>
    </Card>
  );
}
