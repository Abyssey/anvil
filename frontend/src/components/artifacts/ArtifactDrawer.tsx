import { useEffect, useState } from "react";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Heading } from "@astryxdesign/core/Heading";
import { List, ListItem } from "@astryxdesign/core/List";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Section } from "@astryxdesign/core/Section";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Tab, TabList } from "@astryxdesign/core/TabList";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import type { ArtifactKind } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";
import { ABYSS_MARKDOWN_PLUGINS } from "../markdown/markdownPlugins";

export function ArtifactDrawer() {
  const { state, actions } = useWorkspace();
  const [feedback, setFeedback] = useState("");
  const [saveRelPath, setSaveRelPath] = useState("docs/ONBOARDING.md");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const activeSessionId = state.activeSession?.id ?? null;

  useEffect(() => {
    setFeedback("");
    setSaveMessage(null);
  }, [activeSessionId]);

  const activeDoc = state.artifacts.artifacts.find((a) => a.kind === state.activeArtifactTab);
  const pendingPlanReview = state.activeSession?.pending_interrupt?.kind === "plan_review";
  const chunks = state.artifacts.walkthrough_plan?.chunks ?? [];

  const handleSave = async () => {
    if (!activeDoc) return;
    setSaveMessage(null);
    try {
      const savedPath = await actions.saveArtifactToWorkspace(activeDoc.kind, saveRelPath);
      setSaveMessage(`Saved to ${savedPath}`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : "Failed to save artifact");
    }
  };

  return (
    <VStack gap={3} padding={3}>
      <TabList
        value={state.activeArtifactTab}
        onChange={(val) => actions.setActiveArtifactTab(val as ArtifactKind)}
        size="sm"
        hasDivider
      >
        <Tab value="implementation_plan" label="Plan" />
        <Tab value="task" label="Tasks" />
        <Tab value="walkthrough" label="Walkthrough" />
      </TabList>

      {pendingPlanReview && (
        <Banner
          status="info"
          collapsible={false}
          title="Plan ready for review"
          description="Proceed to implement this plan or send revision feedback."
        >
          <VStack gap={2}>
            <TextInput
              label="Revision feedback"
              isLabelHidden
              value={feedback}
              onChange={setFeedback}
              placeholder="Optional feedback to revise the plan..."
            />
            <HStack gap={2} hAlign="end">
              <Button
                label="Cancel"
                variant="ghost"
                size="sm"
                isDisabled={state.isStreaming}
                onClick={() => void actions.resumeInterrupt("deny")}
              />
              <Button
                label="Request Changes"
                variant="secondary"
                size="sm"
                isDisabled={state.isStreaming || !feedback.trim()}
                onClick={() => {
                  const text = feedback.trim();
                  setFeedback("");
                  void actions.resumeInterrupt("revise", text);
                }}
              />
              <Button
                label="Proceed"
                variant="primary"
                size="sm"
                isDisabled={state.isStreaming}
                onClick={() => void actions.resumeInterrupt("approve")}
              />
            </HStack>
          </VStack>
        </Banner>
      )}

      <Section variant="transparent" padding={2}>
        {activeDoc ? (
          <VStack gap={4}>
            <Markdown density="compact" plugins={ABYSS_MARKDOWN_PLUGINS}>
              {activeDoc.content}
            </Markdown>

            {state.activeArtifactTab === "walkthrough" && chunks.length > 0 && (
              <VStack gap={2}>
                <Heading level={4}>Walkthrough Chunks</Heading>
                <List density="compact" hasDividers>
                  {chunks.map((chunk) => (
                    <ListItem
                      key={chunk.id}
                      label={chunk.title}
                      description={chunk.explanation}
                      endContent={
                        <HStack gap={1} wrap="wrap">
                          {chunk.file_hunks.map((fh) => (
                            <Button
                              key={fh.path}
                              label={`${fh.path} (${fh.hunks.join(", ")})`}
                              size="sm"
                              variant="ghost"
                              onClick={() => void actions.jumpToDiffHunk(fh.path, fh.hunks[0])}
                            />
                          ))}
                        </HStack>
                      }
                    />
                  ))}
                </List>
              </VStack>
            )}

            <Collapsible
              defaultIsOpen={false}
              trigger={<Text type="label">Save to Workspace</Text>}
            >
              <VStack gap={2} paddingBlockStart={2}>
                <TextInput
                  label="Target workspace path"
                  value={saveRelPath}
                  onChange={setSaveRelPath}
                  placeholder="docs/ONBOARDING.md"
                />
                <HStack hAlign="start">
                  <Button
                    label="Save File"
                    variant="secondary"
                    size="sm"
                    onClick={() => void handleSave()}
                  />
                </HStack>
                {saveMessage && (
                  <Text type="supporting" color="secondary">
                    {saveMessage}
                  </Text>
                )}
              </VStack>
            </Collapsible>
          </VStack>
        ) : (
          <Text color="secondary">
            No {state.activeArtifactTab.replace("_", " ")} generated yet for this session.
          </Text>
        )}
      </Section>
    </VStack>
  );
}
