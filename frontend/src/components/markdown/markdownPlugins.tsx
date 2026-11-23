import {
  createMarkdownFenceTransform,
  createMarkdownPlugin,
  type MarkdownExtensionNode,
} from "@astryxdesign/core/Markdown/plugins";
import { MermaidDiagram } from "./MermaidDiagram";

type MermaidFenceNode = MarkdownExtensionNode<
  "mermaid-fence",
  "mermaid",
  { readonly code: string; readonly label?: string },
  "block"
>;

function createMermaidPlugin(isFinal: boolean) {
  return createMarkdownPlugin<"mermaid-fence", MermaidFenceNode>({
    name: "mermaid-fence",
    apiVersion: 1,
    transform: createMarkdownFenceTransform({
      languages: ["mermaid", "Mermaid"],
      createNode: ({ code, meta }) => ({
        type: "extension",
        plugin: "mermaid-fence",
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

export const MARKDOWN_PLUGINS = [createMermaidPlugin(true)] as const;

export const STREAMING_MARKDOWN_PLUGINS = [
  createMermaidPlugin(false),
] as const;
