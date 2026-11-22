import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Layout, LayoutContent } from "@astryxdesign/core/Layout";
import { ScrollableArea } from "@astryxdesign/core/ScrollableArea";
import {
  SegmentedControl,
  SegmentedControlItem,
} from "@astryxdesign/core/SegmentedControl";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
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
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  const inlineHostRef = useRef<HTMLDivElement>(null);
  const dialogHostRef = useRef<HTMLDivElement>(null);
  const copyTimerRef = useRef<number | null>(null);

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
    if (!isExpanded || renderState.status !== "ready") {
      host.replaceChildren();
      return;
    }
    const fragment = renderState.module.sanitizeMermaidSvgToFragment(
      renderState.result.svg,
    );
    host.replaceChildren(fragment);
    return () => {
      host.replaceChildren();
    };
  }, [isExpanded, renderState]);

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

  return (
    <Card padding={3} width="100%">
      <VStack gap={2}>
        <HStack gap={2} hAlign="between" vAlign="center" wrap="wrap">
          <HStack gap={1.5} vAlign="center" wrap="wrap">
            <Badge variant="neutral" label={badgeLabel} />
            {renderState.status === "ready" && renderState.result.repaired && (
              <Badge variant="info" label="Auto-fixed" />
            )}
            {renderState.status === "error" && (
              <Badge variant="warning" label="Syntax fallback" />
            )}
          </HStack>

          <HStack gap={1.5} vAlign="center" wrap="wrap">
            {renderState.status === "ready" && (
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
            )}
            <Button
              label={copied ? "Copied" : "Copy"}
              variant="ghost"
              size="sm"
              onClick={() => void handleCopy()}
            />
            {renderState.status === "ready" && (
              <Button
                label="Expand"
                variant="ghost"
                size="sm"
                onClick={() => setIsExpanded(true)}
              />
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
            axis="both"
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
        width="min(92vw, 960px)"
        maxHeight="85dvh"
      >
        <Layout
          height="auto"
          header={
            <DialogHeader
              title={label ? `Mermaid — ${label}` : "Mermaid Diagram"}
              subtitle={
                renderState.status === "ready"
                  ? renderState.result.repaired
                    ? `${renderState.result.diagramType} (auto-fixed)`
                    : renderState.result.diagramType
                  : undefined
              }
              onOpenChange={setIsExpanded}
            />
          }
          content={
            <LayoutContent padding={4}>
              <ScrollableArea
                ref={dialogHostRef}
                axis="both"
                label="Expanded Mermaid diagram"
                padding={2}
              />
            </LayoutContent>
          }
        />
      </Dialog>
    </Card>
  );
}
