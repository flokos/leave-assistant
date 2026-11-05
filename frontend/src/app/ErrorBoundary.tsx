import { Result } from 'antd'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('render error', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return <Result status="error" title="Something went wrong" subTitle={this.state.error.message} />
    }
    return this.props.children
  }
}
