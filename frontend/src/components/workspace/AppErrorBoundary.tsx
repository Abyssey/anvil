import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { VStack } from "@astryxdesign/core/Stack";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  resetKey: number;
}

export class AppErrorBoundary extends Component<Props, State> {
  override state: State = { error: null, resetKey: 0 };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  override componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Error details rendered in accessible alert banner below.
  }

  private handleReset = (): void => {
    this.setState((prev) => ({ error: null, resetKey: prev.resetKey + 1 }));
  };

  override render(): ReactNode {
    if (this.state.error) {
      return (
        <VStack gap={4} padding={6}>
          <Banner
            status="error"
            role="alert"
            collapsible={false}
            title="Workspace UI encountered an unexpected error"
            description={this.state.error.message}
            endContent={
              <Button
                label="Try Again"
                variant="primary"
                size="sm"
                onClick={this.handleReset}
              />
            }
          />
        </VStack>
      );
    }
    return <Fragment key={this.state.resetKey}>{this.props.children}</Fragment>;
  }
}
