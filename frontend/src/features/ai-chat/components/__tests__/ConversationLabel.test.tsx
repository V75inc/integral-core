import { afterEach, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ConversationLabel } from '../ConversationLabel';

afterEach(cleanup);
const title = 'For my saved Volunteer rota venture, prepare the next private rehearsal';

it('uses the full width for a compact single-line title and separates its timestamp', () => {
  render(<button><ConversationLabel title={title} lastActivity="2026-10-08T05:15:00Z" /></button>);
  expect(screen.getByText(title)).toHaveClass('truncate');
  expect(document.querySelector('time')?.parentElement).toHaveClass('block');
});

it('reveals the full name on hover outside the scrolling rail and dismisses it', () => {
  render(<button><ConversationLabel title={title} /></button>);
  fireEvent.mouseEnter(screen.getByTitle(title));
  expect(screen.getByRole('tooltip')).toHaveTextContent(title);
  expect(screen.getByRole('tooltip').parentElement).toBe(document.body);
  fireEvent.mouseLeave(screen.getByTitle(title));
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('reveals for keyboard focus and dismisses on escape, blur, or scroll', () => {
  render(<button><ConversationLabel title={title} /></button>);
  const button = screen.getByRole('button');
  fireEvent.focus(button);
  expect(screen.getByRole('tooltip')).toHaveTextContent(title);
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.focus(button);
  fireEvent.scroll(window);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.focus(button);
  fireEvent.blur(button);
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
});

it('keeps only one reveal open when pointer and keyboard target different rows', () => {
  render(<><button><ConversationLabel title={title} /></button><button><ConversationLabel title="Another conversation" /></button></>);
  fireEvent.mouseEnter(screen.getByTitle(title));
  fireEvent.focus(screen.getByRole('button', { name: 'Another conversation' }));
  expect(screen.getAllByRole('tooltip')).toHaveLength(1);
  expect(screen.getByRole('tooltip')).toHaveTextContent('Another conversation');
});

it('shows authorized useful metadata in the title card', () => {
  render(<button><ConversationLabel title={title} lastActivity="2026-10-08T05:15:00Z" createdAt="2026-10-07T14:00:00Z" messageCount={4} working /></button>);
  fireEvent.focus(screen.getByRole('button'));
  expect(screen.getByRole('tooltip')).toHaveTextContent('Last activity:');
  expect(screen.getByRole('tooltip')).toHaveTextContent('Created:');
  expect(screen.getByRole('tooltip')).toHaveTextContent('4 messages');
  expect(screen.getByRole('tooltip')).toHaveTextContent('Working…');
});
