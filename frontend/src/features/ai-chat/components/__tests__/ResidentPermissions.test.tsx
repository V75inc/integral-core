import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { apiClient } from "../../../../api";
import { tracksApi } from "../../../../api/tracks";
import { PermissionPicker } from "../ResidentPermissions";

it("saves an explicit multi-app selection, reads it back and revokes only the deselected lists", async () => {
  let policy = {mode:"ask", custom_track_ids: [] as string[], revision: ""};
  const get = vi.spyOn(apiClient, "get").mockImplementation(async () => ({data:policy}));
  const put = vi.spyOn(apiClient, "put").mockImplementation(async (_path, body) => {
    const patch = body as {mode:string; track_ids?:string[]};
    policy = {mode:patch.mode, custom_track_ids:patch.track_ids ?? policy.custom_track_ids, revision:String(Number(policy.revision) + 1)};
    return {data:policy};
  });
  const list = vi.spyOn(tracksApi, "list").mockResolvedValue([
    {id:"a", title:"Inbox", app:{id:"p",name:"Projects"}},
    {id:"b", title:"Tasks", app:{id:"p",name:"Projects"}},
    {id:"c", title:"Inbox", app:{id:"s",name:"Sales"}},
    {id:"d", title:"Shared", resident_permission_eligible:false},
  ].map(row => ({workspace_id:"workspace",resident_permission_eligible:true,...row})) as Awaited<ReturnType<typeof tracksApi.list>>);
  try {
    render(<QueryClientProvider client={new QueryClient()}><PermissionPicker user="caller" workspace="workspace" /></QueryClientProvider>);
    const trigger = screen.getByRole("button", {name: /^Permissions/});
    fireEvent.click(trigger);
    const panel = within(await screen.findByRole("dialog", {name:"Permissions"}));
    const ask = () => panel.getByRole("button", {name: /^Ask for approval/});
    const allow = () => panel.getByRole("button", {name: /^Custom/});
    await waitFor(() => expect(allow()).toBeEnabled());
    expect(ask()).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(allow());
    expect(panel.queryByRole("checkbox", {name:"Shared"})).toBeNull();
    fireEvent.click(panel.getByRole("checkbox", {name:"All lists in Projects"}));
    fireEvent.click(panel.getByRole("checkbox", {name:"Tasks"}));
    expect(panel.getByRole("checkbox", {name:"All lists in Projects"})).toBePartiallyChecked();
    fireEvent.click(panel.getByRole("checkbox", {name:"All lists in Sales"}));
    expect(put).not.toHaveBeenCalled();
    fireEvent.click(panel.getByRole("button", {name:"Use Custom"}));
    await waitFor(() => expect(allow()).toHaveAttribute("aria-pressed", "true"));
    expect(allow()).toHaveTextContent("2 saved lists");
    expect(put).toHaveBeenCalledWith("/workspaces/workspace/resident-permission", {mode:"custom",track_ids:["a","c"],revision:""});
    fireEvent.click(allow());
    const sales = () => panel.getByRole("checkbox", {name:"All lists in Sales"});
    expect(sales()).toBeChecked();
    fireEvent.click(sales());
    fireEvent.click(panel.getByRole("button",{name:"Back to permissions"}));
    fireEvent.click(allow());
    expect(sales()).toBeChecked();
    fireEvent.click(sales());
    fireEvent.click(panel.getByRole("button", {name:"Save"}));
    await waitFor(() => expect(allow()).toHaveTextContent("1 saved list"));
    expect(put).toHaveBeenLastCalledWith("/workspaces/workspace/resident-permission",{mode:"custom",track_ids:["a"],revision:"1"});
    fireEvent.click(ask());
    await waitFor(() => expect(ask()).toHaveAttribute("aria-pressed", "true"));
    expect(put).toHaveBeenLastCalledWith("/workspaces/workspace/resident-permission",{mode:"ask",revision:"2"});
    for (const name of ["Approve for me", "Full access", "Ask for approval"]) {
      const row = panel.getByRole("button", {name:new RegExp("^"+name)});
      fireEvent.click(row);
      await waitFor(() => expect(row).toHaveAttribute("aria-pressed","true"));
      expect(allow()).toHaveTextContent("1 saved list");
    }
    fireEvent.click(allow());
    expect(panel.getByRole("checkbox", {name:"All lists in Projects"})).toBePartiallyChecked();
    fireEvent.click(panel.getByRole("button",{name:"Use Custom"}));
    await waitFor(() => expect(allow()).toHaveAttribute("aria-pressed","true"));
  } finally { get.mockRestore(); put.mockRestore(); list.mockRestore(); }
});
