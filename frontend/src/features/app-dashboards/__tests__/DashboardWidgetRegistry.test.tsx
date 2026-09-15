import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ChartBarWidget,
  ChartPieWidget,
  DashboardWidgetRenderer,
  MetricCardWidget,
} from '../DashboardWidgetRegistry';

vi.mock('recharts', async () => {
  const React = await import('react');
  const passthrough = ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="recharts-mock">{children}</div>
  );
  return {
    ResponsiveContainer: passthrough,
    BarChart: passthrough,
    Bar: () => <div data-testid="bar" />,
    LineChart: passthrough,
    Line: () => null,
    PieChart: passthrough,
    Pie: ({ children }: { children?: React.ReactNode }) => (
      <div data-testid="pie">{children}</div>
    ),
    Cell: () => null,
    CartesianGrid: () => null,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Legend: () => <div data-testid="chart-legend" />,
  };
});

describe('DashboardWidgetRenderer', () => {
  it('renders metric_card with value', () => {
    render(
      <MetricCardWidget title="Open items" data={{ value: 42 }} />,
    );
    expect(screen.getByText('Open items')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders metric_card suffix from config', () => {
    render(
      <MetricCardWidget
        title="Revenue"
        data={{ value: 1200 }}
        config={{ suffix: 'USD' }}
      />,
    );
    expect(screen.getByText('1200')).toBeInTheDocument();
    expect(screen.getByText('USD')).toBeInTheDocument();
  });

  it('shows error state when data.error is set', () => {
    render(
      <MetricCardWidget
        title="Broken"
        data={{ error: 'track not found' }}
      />,
    );
    expect(screen.getByText(/Couldn't load/)).toBeInTheDocument();
    expect(screen.getByText(/track not found/)).toBeInTheDocument();
  });

  it('renders horizontal bar chart with legend when configured', () => {
    render(
      <ChartBarWidget
        title="Statuses"
        config={{ orientation: 'horizontal', show_legend: true }}
        data={{
          series: [
            { label: 'Open', value: 3 },
            { label: 'Done', value: 7 },
          ],
        }}
      />,
    );
    expect(screen.getByText('Statuses')).toBeInTheDocument();
    expect(screen.getByTestId('chart-legend')).toBeInTheDocument();
  });

  it('renders pie chart legend by default', () => {
    render(
      <ChartPieWidget
        title="Mix"
        data={{
          series: [
            { label: 'A', value: 1 },
            { label: 'B', value: 2 },
          ],
        }}
      />,
    );
    expect(screen.getByTestId('chart-legend')).toBeInTheDocument();
  });

  it('falls back for unknown widget type', () => {
    render(
      <DashboardWidgetRenderer
        type="unknown_widget"
        title="Test"
        data={{}}
      />,
    );
    expect(screen.getByText(/Unknown widget/)).toBeInTheDocument();
  });
});
