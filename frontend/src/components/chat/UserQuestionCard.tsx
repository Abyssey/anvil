import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { useHotkeys, type Hotkey } from "@astryxdesign/core/hooks";
import { Icon } from "@astryxdesign/core/Icon";
import { IconButton } from "@astryxdesign/core/IconButton";
import { Kbd } from "@astryxdesign/core/Kbd";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { QuestionMarkCircleIcon } from "@heroicons/react/20/solid";
import type { PendingInterrupt, QuestionAnswer, UserQuestionItem } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

interface StepDraft {
  selectedOption: string | null;
  customText: string;
  useCustom: boolean;
}

function emptyDraft(): StepDraft {
  return { selectedOption: null, customText: "", useCustom: false };
}

function isInteractiveElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return Boolean(
    target.closest(
      'button, a, input, textarea, select, [role="button"], [contenteditable="true"]',
    ),
  );
}

function readNarrowViewport(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(max-width: 560px)").matches;
}

export interface UserQuestionCardProps {
  pending: PendingInterrupt;
}

export function UserQuestionCard({ pending }: UserQuestionCardProps) {
  const { state, actions } = useWorkspace();
  const questions: readonly UserQuestionItem[] = useMemo(() => {
    if (Array.isArray(pending.questions) && pending.questions.length > 0) {
      return pending.questions;
    }
    return [{ question: pending.summary, options: [] }];
  }, [pending.questions, pending.summary]);

  const questionSignature = useMemo(
    () => questions.map((q) => `${q.question}:${q.options.length}`).join("|"),
    [questions],
  );

  const [stepIndex, setStepIndex] = useState(0);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isNarrowViewport, setIsNarrowViewport] = useState<boolean>(readNarrowViewport);
  const [drafts, setDrafts] = useState<StepDraft[]>(() => questions.map(() => emptyDraft()));
  const otherInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia("(max-width: 560px)");
    const onChange = (e: MediaQueryListEvent) => setIsNarrowViewport(e.matches);
    setIsNarrowViewport(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    setStepIndex(0);
    setIsCollapsed(false);
    setDrafts(questions.map(() => emptyDraft()));
  }, [questionSignature, questions]);

  const currentQuestion = questions[stepIndex] ?? questions[0];
  const currentDraft = drafts[stepIndex] ?? emptyDraft();
  const isMultiStep = questions.length > 1;
  const isLastStep = stepIndex >= questions.length - 1;

  const currentAnswerText = currentDraft.useCustom
    ? currentDraft.customText.trim()
    : (currentDraft.selectedOption ?? "").trim();
  const canAdvance = currentAnswerText.length > 0 && !state.isStreaming;

  const updateCurrentDraft = useCallback(
    (updater: (prev: StepDraft) => StepDraft) => {
      setDrafts((prev) =>
        prev.map((item, idx) => (idx === stepIndex ? updater(item) : item)),
      );
    },
    [stepIndex],
  );

  const selectOption = useCallback(
    (optionLabel: string) => {
      updateCurrentDraft((prev) => ({
        ...prev,
        selectedOption: optionLabel,
        useCustom: false,
      }));
    },
    [updateCurrentDraft],
  );

  const handleOtherChange = useCallback(
    (value: string) => {
      updateCurrentDraft((prev) => ({
        ...prev,
        customText: value,
        useCustom: value.trim().length > 0 ? true : prev.selectedOption === null,
      }));
    },
    [updateCurrentDraft],
  );

  const buildAnswersPayload = useCallback(
    (allSkipped = false): QuestionAnswer[] =>
      questions.map((item, idx) => {
        if (allSkipped) {
          return { question: item.question, skipped: true };
        }
        const draft = drafts[idx] ?? emptyDraft();
        const custom = draft.customText.trim();
        if (draft.useCustom && custom) {
          return { question: item.question, custom_text: custom, skipped: false };
        }
        if (draft.selectedOption) {
          return {
            question: item.question,
            selected_option: draft.selectedOption,
            skipped: false,
          };
        }
        if (custom) {
          return { question: item.question, custom_text: custom, skipped: false };
        }
        return { question: item.question, skipped: true };
      }),
    [drafts, questions],
  );

  const handlePrimaryAction = useCallback(() => {
    if (!canAdvance) return;
    if (!isLastStep) {
      setStepIndex((idx) => Math.min(idx + 1, questions.length - 1));
      return;
    }
    void actions.resumeInterrupt("answer", undefined, buildAnswersPayload(false));
  }, [actions, buildAnswersPayload, canAdvance, isLastStep, questions.length]);

  const handleSkip = useCallback(() => {
    if (state.isStreaming) return;
    void actions.resumeInterrupt("skip", undefined, buildAnswersPayload(true));
  }, [actions, buildAnswersPayload, state.isStreaming]);

  const otherIndex = currentQuestion.options.length + 1;
  const shortcutsDisabled = isCollapsed || state.isStreaming;

  const hotkeys = useMemo(() => {
    const entries: Hotkey[] = currentQuestion.options.slice(0, 8).map((opt, idx) => ({
      keys: String(idx + 1),
      isDisabled: shortcutsDisabled,
      onPress: () => selectOption(opt.label),
    }));
    if (otherIndex <= 9) {
      entries.push({
        keys: String(otherIndex),
        isDisabled: shortcutsDisabled,
        onPress: () => {
          updateCurrentDraft((prev) => ({ ...prev, useCustom: true }));
          otherInputRef.current?.focus();
        },
      });
    }
    entries.push({
      keys: "mod+enter",
      allowInInputs: true,
      isDisabled: shortcutsDisabled || !canAdvance,
      onPress: handlePrimaryAction,
    });
    return entries;
  }, [
    canAdvance,
    currentQuestion.options,
    handlePrimaryAction,
    otherIndex,
    selectOption,
    shortcutsDisabled,
    updateCurrentDraft,
  ]);

  useHotkeys(hotkeys);

  useEffect(() => {
    if (shortcutsDisabled || !canAdvance) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key !== "Enter" ||
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.shiftKey ||
        event.isComposing ||
        event.keyCode === 229 ||
        isInteractiveElement(event.target)
      ) {
        return;
      }
      event.preventDefault();
      handlePrimaryAction();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canAdvance, handlePrimaryAction, shortcutsDisabled]);

  return (
    <Card padding={3} elevation="none">
      <VStack gap={1.5} width="100%">
        <HStack gap={2} vAlign="start" width="100%">
          <StackItem size="static">
            <Icon icon={QuestionMarkCircleIcon} size="md" color="secondary" />
          </StackItem>

          <StackItem size="fill">
            <Text type="body" weight="semibold">
              {currentQuestion.question}
            </Text>
          </StackItem>

          <StackItem size="static">
            <HStack gap={0.5} vAlign="center">
              {isMultiStep && (
                <Badge label={`${stepIndex + 1} / ${questions.length}`} variant="neutral" />
              )}
              <IconButton
                label={isCollapsed ? "Expand question" : "Collapse question"}
                variant="ghost"
                size="sm"
                icon={<Icon icon={isCollapsed ? "chevronRight" : "chevronDown"} />}
                onClick={() => setIsCollapsed((prev) => !prev)}
              />
              <IconButton
                label="Dismiss question"
                variant="ghost"
                size="sm"
                icon={<Icon icon="close" />}
                isDisabled={state.isStreaming}
                onClick={handleSkip}
              />
            </HStack>
          </StackItem>
        </HStack>

        {!isCollapsed && (
          <VStack gap={1.5} width="100%">
            {currentQuestion.options.length > 0 && (
              <List
                density="compact"
                edge="outset"
                aria-label={currentQuestion.question}
              >
                {currentQuestion.options.map((opt, idx) => {
                  const isSelected =
                    !currentDraft.useCustom && currentDraft.selectedOption === opt.label;
                  const displayText = opt.recommended
                    ? `(Recommended) ${opt.label}`
                    : opt.label;
                  return (
                    <ListItem
                      key={`${idx}-${opt.label}`}
                      align="first-line"
                      isSelected={isSelected}
                      isDisabled={state.isStreaming}
                      startContent={<Kbd keys={String(idx + 1)} />}
                      label={
                        <Text
                          type="body"
                          weight={isSelected ? "semibold" : undefined}
                        >
                          {displayText}
                        </Text>
                      }
                      description={
                        opt.description ? (
                          <Text type="supporting" color="secondary">
                            {opt.description}
                          </Text>
                        ) : undefined
                      }
                      endContent={
                        isSelected ? <Icon icon="check" size="sm" /> : undefined
                      }
                      onClick={() => selectOption(opt.label)}
                    />
                  );
                })}
              </List>
            )}

            {isNarrowViewport ? (
              <VStack gap={1.5} width="100%">
                <HStack gap={2} vAlign="center" width="100%">
                  <StackItem size="static">
                    <Kbd keys={String(otherIndex)} />
                  </StackItem>
                  <StackItem size="fill">
                    <TextInput
                      ref={otherInputRef}
                      label="Other (write your answer)"
                      isLabelHidden
                      size="sm"
                      width="100%"
                      value={currentDraft.customText}
                      isDisabled={state.isStreaming}
                      placeholder="Other (write your answer)"
                      enterKeyHint={isLastStep ? "send" : "next"}
                      hasClear
                      onChange={handleOtherChange}
                      onEnter={handlePrimaryAction}
                    />
                  </StackItem>
                </HStack>

                <HStack gap={1} vAlign="center" hAlign="end" width="100%">
                  {isMultiStep && stepIndex > 0 && (
                    <Button
                      label="Back"
                      variant="ghost"
                      size="sm"
                      isDisabled={state.isStreaming}
                      onClick={() => setStepIndex((idx) => Math.max(0, idx - 1))}
                    />
                  )}
                  <Button
                    label="Skip"
                    variant="ghost"
                    size="sm"
                    isDisabled={state.isStreaming}
                    onClick={handleSkip}
                  />
                  <Button
                    label={isLastStep ? "Submit" : "Next"}
                    variant="primary"
                    size="sm"
                    isDisabled={!canAdvance}
                    onClick={handlePrimaryAction}
                  />
                </HStack>
              </VStack>
            ) : (
              <HStack gap={2} vAlign="center" width="100%">
                <StackItem size="static">
                  <Kbd keys={String(otherIndex)} />
                </StackItem>

                <StackItem size="fill">
                  <TextInput
                    ref={otherInputRef}
                    label="Other (write your answer)"
                    isLabelHidden
                    size="sm"
                    width="100%"
                    value={currentDraft.customText}
                    isDisabled={state.isStreaming}
                    placeholder="Other (write your answer)"
                    enterKeyHint={isLastStep ? "send" : "next"}
                    hasClear
                    onChange={handleOtherChange}
                    onEnter={handlePrimaryAction}
                  />
                </StackItem>

                <StackItem size="static">
                  <HStack gap={1} vAlign="center">
                    {isMultiStep && stepIndex > 0 && (
                      <Button
                        label="Back"
                        variant="ghost"
                        size="sm"
                        isDisabled={state.isStreaming}
                        onClick={() => setStepIndex((idx) => Math.max(0, idx - 1))}
                      />
                    )}
                    <Button
                      label="Skip"
                      variant="ghost"
                      size="sm"
                      isDisabled={state.isStreaming}
                      onClick={handleSkip}
                    />
                    <Button
                      label={isLastStep ? "Submit" : "Next"}
                      variant="primary"
                      size="sm"
                      isDisabled={!canAdvance}
                      onClick={handlePrimaryAction}
                    />
                  </HStack>
                </StackItem>
              </HStack>
            )}
          </VStack>
        )}
      </VStack>
    </Card>
  );
}
