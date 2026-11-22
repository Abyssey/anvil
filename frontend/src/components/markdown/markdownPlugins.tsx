import {
  createMarkdownFenceTransform,
  createMarkdownPlugin,
  type MarkdownExtensionNode,
} from "@astryxdesign/core/Markdown/plugins";
import { MermaidDiagram } from "./MermaidDiagram";

type MermaidFenceNode = MarkdownExtensionNode<
  "abyss-mermaid",
  "mermaid",
  { readonly code: string; readonly label?: string },
  "block"
>;

function createAbyssMermaidPlugin(isFinal: boolean) {
  return createMarkdownPlugin<"abyss-mermaid", MermaidFenceNode>({
    name: "abyss-mermaid",
    apiVersion: 1,
    transform: createMarkdownFenceTransform({
      languages: ["mermaid", "Mermaid"],
      createNode: ({ code, meta }) => ({
        type: "extension",
        plugin: "abyss-mermaid",
        name: "mermaid",
        display: "block",
        data: {
          code,
          ...(meta && meta.trim().length > 0 ? { label: meta.trim() } : {}),
        },
      }),
    }),
    renderers: {
      mermaid: {
        render: ({ node }) => (
          <MermaidDiagram
            code={node.data.code}
            label={node.data.label}
            isFinal={isFinal}
          />
        ),
        toText: (node) => node.data.code,
      },
    },
  });
}

export const ABYSS_MARKDOWN_PLUGINS = [createAbyssMermaidPlugin(true)] as const;

export const ABYSS_STREAMING_MARKDOWN_PLUGINS = [
  createAbyssMermaidPlugin(false),
] as const;
