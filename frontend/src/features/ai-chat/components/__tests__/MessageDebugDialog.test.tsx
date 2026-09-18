import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MessageDebugDialog } from "../MessageDebugDialog";

vi.mock("../../../../context/ThemeContext", () => ({
  useTheme: () => ({ theme: "light" }),
}));

describe("MessageDebugDialog", () => {
  it("shows QuerySpec provenance and its receipt reference", () => {
    render(
      <MessageDebugDialog
        messageContent="Done"
        payload={{
          claim_provenance: {
            tools: [
              {
                name: "integral_query_spec",
                source: "query",
                status: "complete",
                result_set_id: "result-set-1",
                run_id: "query-run-1",
                graph_revision: "sha256:graph-revision",
                receipt: {
                  run_id: "query-run-1",
                  step_key: "capability:query-step",
                },
              },
            ],
          },
        }}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Query result provenance" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Run receipt" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/result-set-1/).length).toBeGreaterThan(0);
  });

  it("never labels page context as a result set", () => {
    render(
      <MessageDebugDialog
        messageContent="Done"
        payload={{
          claim_provenance: {
            tools: [
              {
                name: "integral_get_page_context",
                source: "page_context",
                status: "complete",
                result_set_id: "spoofed-page-result-set",
              },
            ],
          },
        }}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("heading", { name: "Query result provenance" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Run receipt" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Claim provenance" }),
    ).toBeInTheDocument();
  });
});
