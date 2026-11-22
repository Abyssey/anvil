import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Icon } from "@astryxdesign/core/Icon";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  QuestionMarkCircleIcon,
} from "@heroicons/react/24/outline";
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

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable
  );
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
  const [drafts, setDrafts] = useState<StepDraft[]>(() => questions.map(() => emptyDraft()));
  const otherInputRef = useRef<HTMLInputElement>(null);

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

  useEffect(() => {
    if (isCollapsed || state.isStreaming) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isEditableTarget(e.target)) {
        return;
      }
      if (/^[1-9]$/.test(e.key)) {
        const num = Number(e.key);
        const opt = currentQuestion.options[num - 1];
        if (opt) {
          e.preventDefault();
          selectOption(opt.label);
        } else if (num === currentQuestion.options.length + 1) {
          e.preventDefault();
          updateCurrentDraft((prev) => ({ ...prev, useCustom: true }));
          otherInputRef.current?.focus();
        }
        return;
      }
      if (e.key === "Enter" && canAdvance) {
        e.preventDefault();
        handlePrimaryAction();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    canAdvance,
    currentQuestion.options,
    handlePrimaryAction,
    isCollapsed,
    selectOption,
    state.isStreaming,
    updateCurrentDraft,
  ]);

  const otherIndex = currentQuestion.options.length + 1;
  const isOtherSelected = currentDraft.useCustom && currentDraft.customText.trim().length > 0;

  return (
    <Card padding={3} elevation="low">
      <VStack gap={2} width="100%">
        <HStack hAlign="between" vAlign="start" gap={2} width="100%">
          <HStack gap={2} vAlign="start">
            <Icon icon={QuestionMarkCircleIcon} size="sm" color="secondary" />
            <Text type="label" weight="semibold">
              {currentQuestion.question}
            </Text>
          </HStack>

          <HStack gap={1.5} vAlign="center">
            {isMultiStep && (
              <Badge label={`${stepIndex + 1} / ${questions.length}`} variant="neutral" />
            )}
            <Button
              label={isCollapsed ? "Expand question" : "Collapse question"}
              variant="ghost"
              size="sm"
              isIconOnly
              icon={
                <Icon
                  icon={isCollapsed ? ChevronUpIcon : ChevronDownIcon}
                  size="sm"
                />
              }
              onClick={() => setIsCollapsed((prev) => !prev)}
            />
          </HStack>
        </HStack>

        {!isCollapsed && (
          <VStack gap={2} width="100%">
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
                    startContent={
                      <Badge
                        label={String(idx + 1)}
                        variant={isSelected ? "info" : "neutral"}
                      />
                    }
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
                      isSelected ? <Icon icon={CheckIcon} size="sm" /> : undefined
                    }
                    onClick={() => selectOption(opt.label)}
                  />
                );
              })}

              <ListItem
                align="center"
                isSelected={isOtherSelected}
                isDisabled={state.isStreaming}
                interactiveRef={otherInputRef}
                startContent={
                  <Badge
                    label={String(otherIndex)}
                    variant={isOtherSelected ? "info" : "neutral"}
                  />
                }
                label={
                  <TextInput
                    ref={otherInputRef}
                    label="Other (write your answer)"
                    isLabelHidden
                    size="sm"
                    value={currentDraft.customText}
                    isDisabled={state.isStreaming}
                    placeholder="Other (write your answer)"
                    onChange={handleOtherChange}
                    onEnter={handlePrimaryAction}
                  />
                }
              />
            </List>

            <HStack hAlign="between" vAlign="center" gap={2} width="100%">
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
              </HStack>

              <HStack gap={1.5} vAlign="center">
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
            </HStack>
          </VStack>
        )}
      </VStack>
    </Card>
  );
}
