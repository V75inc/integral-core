# Substrate improvement status — 28 Sep 2026

Live score of [CORE_SUBSTRATE_IMPROVEMENT_PLAN.md](../product/CORE_SUBSTRATE_IMPROVEMENT_PLAN.md). That file stays the 26 Sep snapshot. This copy is what to test next.

Scored from david.henry@v75inc.com’s org workspace (`n.Workspace.b17f81f9860e45a5987d012d`) and the Car Rental Desk there (`n.WorkspaceApp.dcc482614def4a5bad99b9c2`). A chat that says it worked does not count. The object was opened, or the row stays unconfirmed.

| Status | Meaning |
| --- | --- |
| **Confirmed** | A live chat on this stack, then the object was opened. |
| **In code** | The branch has it. This pass did not prove it on screen. |
| **Failed live** | We asked, and the saved result was not the job. |
| **Not built** | Still absent, including the closed gates. |

Sessions A–C are scored in the package list below. Wave 4 rename, move refusal, tag rename, and Improve this are confirmed. The daily-rate dashboard is confirmed. An open batch resumed once. D1–D3 stay closed.

## Where it stands

**26 of 38 work packages are confirmed** on this workspace. That is 68%. Another **7** are in the branch and were not opened as an object (about 18%). **5** were left unfinished on purpose. Counting the four gates, which stay closed, confirmed completion is 26 of 42.

| State | What |
| --- | --- |
| Confirmed on the app | Waves 1 and 2, nearly all of Wave 3, Wave 4’s rename, move refusal, tag rename, and Improve this, and the Daily rates dashboard (12080). The two shortcuts stay marked unavailable, which is the pass for that package. |
| In code, not opened | Drift checks, the capability map, the qualification corpus, the query boundary, `table_widget` and `progress`, skill routing, and the Settings / Mission Control lists. The live dashboard used the metric card. |
| Waiting on a written decision | **D1** save an app as a library package. **D2** a question that walks more than one link. **D3** a resident-written live tool. These start only after an approved scoped design. |
| Already decided | **D4.** Field edits stay on the draft. `integral_modify_model` does not gain field edits. A renamed key is `rename_field`. |
| Not this plan | The payroll app that says setup is missing when the data is there, and the custom iframe gaps. The live qualification exam (W0.3b) needs the held-out questions from Q. |

---

## Already confirmed (do not re-ask)

| Package | What was opened |
| --- | --- |
| **W1.1 Tags in the build** | Vehicles carried Economy, SUV, and Van. Economy was later renamed to Compact. SUV and Van stayed. |
| **W1.3 Structured blueprint** (seeds) | Toyota Corolla, Jane Doe, and a rental that stores both ids. |
| **W2.3 No-fit route** | A lunch note was not filed into Vehicles, Customers, or Rentals. A new type was proposed. |
| **W2.1 / W2.2 filing** | Sam Patel and a Honda Civic were staged as two filings. |
| **W3.1 Aggregation** | One vehicle, daily rate 10000: total 10000, average 10000. |
| **W3.2 Business sort** | Highest daily rate is the Toyota Corolla. |
| **W3.5 Query planner** | The Jane Doe rental was named as due back on 2026-09-30. |
| **W3.4 refusal** | “Customers whose vehicles are due for service” was refused as a multi-hop question. That refusal is the current product. The traversal itself is Not built. |
| **W1 existing-app track** | `n.ChatThread.393b04032f3642aba76f2599` replied “The Maintenance track is waiting for approval.” The card was `create_track` Maintenance on Car Rental Desk. After bless, the app has track `n.Track.1f05c10e96c34c35afd9abb3`. |
| **W1.2 Anchors** | One card registered the Service Log template on Vehicles. After bless, `n.Track.fccde20a8ee449d3aa5d9608` is “Service Log: Toyota Corolla” and `n.Track.8ad6ab056e7042ffab0a92dc` is “Service Log: Honda Civic”. |
| **W1.7 Phase A** | The reply was “The return add-on is specified and not installed.” The spec is stored on the thread. It is not a live tool. |
| **W3.6 Follow-up** | The same chat that said “Economy 1, SUV 0, Van 0” then answered “Toyota Corolla, daily rate 12000.” |
| **W4.1 Rename** | Customers `phone` is now `mobile`. Jane Doe’s number `555-9999` is on `mobile`. |
| **W4.4 Tag rename** | Economy is now Compact (`n.Tag.68a3e64ddff14c228eeff252`). Toyota Corolla still carries that tag. SUV and Van were not merged. |
| **W4.5 Improve this** | `n.ChatThread.9b9b9c52d7e844eba5cc8399` staged one card: a table for Daily Rate. The Vehicles draft `n.OperationalModel.9f5bd5f682274f1d889d5065` now has view `daily_rate_table`. |
| **W5 Dashboard** | Dashboard `n.Dashboard.137c2f8d536f4eb584e9c298` “Daily rates” sums `daily_rate` to 12080. The rows are Toyota Corolla 12000 and Honda Civic 80. |
| **Date on the field** | `n.ChatThread.8fcf3cd056db4c549be66727` staged an update of “Sandy Singh - Appointment on October 4th”. After bless, `n.Entry.415ae45a20a64712a93bb393` `date` is `2026-10-01`. The other Sandy row stays `2026-10-01`. Still two appointments. |

## Failed live (do not mark done)

These were asked. The saved result was not the job.

| What we asked | What happened | Package |
| --- | --- | --- |
| Rename phone to mobile | Confirmed. The card was `rename_field` phone → mobile on Customers. Jane Doe’s `555-9999` is on `mobile`. | W4.1 |
| Move the rental | Confirmed as a named refusal. `n.ChatThread.30b922fe26b049b18e825ad3` refused the move to Maintenance because that track has no customer, due_back_date, start_date, status, or vehicle field. The rental is still on Rentals. | W4.3 |
| Automatic texts | Confirmed. `n.ChatThread.0f466760b92640208576a00d` replied that sending a text requires a trusted package and nothing was staged. No routine card. | W1.4 honesty |
| Keep Sandy’s October appointment and delete the duplicates | First ask created a cleanup-titled appointment. Leave that row if it is still there. After `integral_delete_entry` was pinned, `n.ChatThread.303c3d0c331e48179759c71a` deleted the October 1st duplicate and kept October 4th. No new cleanup-titled appointment. | W2.2 |
| “What is this connected to” | Confirmed. With the Jane Doe rental open (`n.ChatThread.bb942290664d46c583321f59`) the reply is “Connected to Jane Doe and Toyota Corolla.” | W3.3 |
| Counts by tag | Confirmed. `n.ChatThread.eb635c16980b40d38af4b222` replied “Economy 1, SUV 0, Van 0.” The earlier “class” clarifying question is the same package and was not repeated. | W3.1 |

In code today, not re-proven by a new build: `board` counts as `kanban`; the fidelity refusal no longer says “retry now”; `integral_verify_build` carries a `reply` sentence; the approval card shows `detail`; `add_entry_type` accepts top-level `name` and `fields`. The Rentals board already has a Reserved column, so the builder was not changed.

`integral_update_entry` and `integral_delete_entry` are pinned next to the create tools. The Jane update and the Sandy delete both staged the existing rows. A texting request now answers before the model starts. A tag count’s reply is the tool sentence, with tag names. An open entry’s connections are read when the question is “what is this connected to.” Adding a named track while an app is focused stages that one card before the model starts, so the design proposal does not open.

## Bugs that are not in the improvement plan

These showed up while walking the plan. They are not a wave, a gate, or a package in the 26 Sep document. The skills on `/agent` were already the live files. The Python that staged the card lives in the API image.

| Bug | What happened | Where it stands |
| --- | --- | --- |
| A later track on an existing app is staged as `{{app.id}}` | “Add Maintenance” became a card for “App the new app.” Policy then said “Cannot add a track to this app.” `{{app.id}}` is only valid inside a batch that creates the app. The server rewrote the app name to that token. The scaffold skill already said to use the real id. | Confirmed on the focused app. `n.ChatThread.393b04032f3642aba76f2599` staged Maintenance on `n.WorkspaceApp.dcc482614def4a5bad99b9c2` and did not propose a design. A batch that creates an app still proposes first. |
| `modify_field` rejects the shape `add_field` already accepts | The card was approved and the server answered `modify_field requires entry_type + field_key + patch`. The model sent `entry_type_key`, `field`, and a top-level `name`. Jane Doe’s number stayed on phone. Keeping the stored value under a new key is Wave 4 and is still not built. This row is the call the server would not accept. | Aliases are accepted in the 19:37 image. An incomplete op is refused before a card. The re-ask never sent `modify_field`. |
| A cleanup sentence is saved as a new entry | “Keep Sandy’s October appointment and delete the duplicates” approved a create titled “Clean up duplicate appointments for Sandy,” with empty fields. The October duplicate and the 30 September row stayed. That created row stays. Do not delete it to score this. | Create and create-mode filing refuse that sentence before a card, in the 19:37 image. The re-ask called no tool, and no new cleanup-titled row appeared. |
| A made-up track id is reported as a permissions problem | On the phone re-ask, `integral_describe_model` was called with `n.Track.1e8b6b7b3c5d4e2b8d9f7a6c`. That id is not a Customers track. The tool returned “Policy denied track.read.” The reply said the Customers schema could not be read. | The deny is what the tool returned for that id. The reply treated it as missing admin rights on Customers. |
| An update is staged as a new entry | The tools pinned on every turn were only the create tools, so an update became a new title. Pinning update and delete fixed a named update. A later ask still created “Sandy Singh Appointment” because the new title was not an exact match, and create is pinned so the model does not have to look first. | Confirmed on Appointments (`n.Track.5a384dc2c225427ba77170b8`). A create whose name already appears on that track is refused before a card. `n.ChatThread.e8ba76057d904ede935db360` asked for “Follow-up with Sandy Singh” on 1 October. The create was refused. The reply named the existing appointment and staged an update, which was revoked. Both rows remain: `n.Entry.4f65e351955042c1ac41bc5e` “Sandy Singh Appointment” and `n.Entry.415ae45a20a64712a93bb393` “Sandy Singh - Appointment on October 4th”. A second record is staged only when the person’s own sentence asks for another one. The model’s flag does not skip the check. An unrelated title such as “Van service” is not treated as Sandy. A stated date left only in the title is refused. `n.ChatThread.8fcf3cd056db4c549be66727` set `date` on the October 4th row to `2026-10-01`. The standing role says a date, time, phone, or amount goes on the matching field and the title stays the name. |
| The approval card hid the server’s reason | A failed bless showed “Server write failed.” The payload already had `modify_field requires entry_type + field_key + patch`. | The card shows `detail`. That web image was built earlier today. Not re-opened after this API rebuild. |
| The reply describes a finished job when the tool did something else | The 17:59 verify was partial and the chat still said the app was set up. The tag-count reply said no vehicle was Economy after the tool had counted one. | Tag counts now show the tool sentence (`Economy 1, SUV 0, Van 0`). A texting request is answered before the model writes a routine. Other jobs can still narrate past a tool result. |
| The seed rule fought the skill, then the view type | The build looped because fidelity said to leave unspecified values blank while the skill said to leave those fields absent. After that, a planned `board` did not match the saved `kanban`, and a rewritten seed title failed on purpose. | Blank-versus-absent and the “retry now” sentence are in code. `board` counts as `kanban` in code. A rewritten title still fails. Not re-proven by a new build. |
| The verifier named `rentals.status` missing while Reserved was a column | The Rentals board groups on `custom_fields.status`. The columns are Reserved, Out, and Returned. The saved rental is Reserved. The node-level view type was empty and the status options are stored as `enum`. | The builder was not changed. The column is there. |
| A packaged payroll app reads an empty generic query as “setup is not done” | Guyana Pay Runs and payroll settings were on screen. `integral_query_entries` on a packaged app returns an empty list because of the query boundary. The skill spoke the missing-setup script. The same step is in the other payroll apps. | Written as its own plan. Not implemented. Opening generic query on packaged tracks would undo the boundary. |
| A Friday build said Car Rental Desk existed after the batch aborted | A seed token `{{track.id:Vehicles}}` plus the focused view made the track lookup fail. The batch aborted. The chat said the app was there. It was not in that workspace. The 17:59 build in this org did save. | Did not recur on the saved app. The abort path is still the one that must not be narrated as success. |

---

## Test the rest

Scored on 28 Sep. Results are in the package list above. The prompts stay here as the record of what was asked.

One new chat per prompt. Stay in the org workspace. Stay on Car Rental Desk unless the prompt says Appointments. Open the object after the reply. Record asked / claimed / actually exists.

Skip dashboards (Wave 5). Skip save-as-package (D1), multi-hop (D2 / W3.4), and a live executable tool (D3). Skip the three failed writes above until the guards are rebuilt.

### Session A — design and build still only in code

| Order | Package | Prompt | Pass |
| --- | --- | --- | --- |
| A1 | W1.2 Anchors | “On Car Rental Desk, each vehicle should have its own service-log track, one log per vehicle, not one shared log.” | Two vehicles would get two detail tracks from one template. If it only proposes, that is a proposal, not a pass. |
| A2 | W1.4 Coverage | “Add a live map of where each vehicle is.” | Refused before a card. The reply says the map is unsupported. |
| A3 | W1.5 Verify | Open the existing Rentals board. Do not rebuild the app. | Reserved, Out, and Returned are columns. A chat that says the board is missing Reserved is wrong; the column is there. |
| A4 | W1.6 Discovery | In a new chat with no app focused: “Run the car rental skill.” | It names Car Rental Desk and asks before acting. It does not invent a workspace-wide skill. |
| A5 | W1.7 Phase A | “When a rental is returned, record a protected return step that a package would perform.” | An operation spec is stored. The reply says the add-on is specified, not installed. |
| A6 | W0.2 / W0.3 | A short correction in the same chat after A1: “No, service logs only, drop anything else you added.” | The next proposal is a diff of the previous one. |

### Session B — capture still only in code

| Order | Package | Prompt | Pass |
| --- | --- | --- | --- |
| B1 | W2.4 Skill precedence | “New hire: Priya Shah, starts Monday, engineer.” | It offers the domain skill if one is installed. It does not file Priya into Customers. |
| B2 | W2.2 Update | “Jane Doe’s phone is now 555-9999. Update her, do not add another customer.” | The existing Jane Doe row changes. No second Jane Doe. |
| B3 | W3.9 Field keys | “Set the Toyota Corolla daily rate using the label Daily rate, value 12000.” | The stored `daily_rate` is 12000, or the card is refused before approval. |

### Session C — questions still only in code

| Order | Package | Prompt | Pass |
| --- | --- | --- | --- |
| C1 | W3.1 count | “How many vehicles are there, by tag: Economy, SUV, and Van?” | Three counts, matching the tags on the vehicles. No clarifying question about the word class. |
| C2 | W3.3 Relations | Open the Jane Doe rental, then: “What is this rental connected to?” | The reply names the customer and the vehicle. A 422 is a fail. |
| C3 | W3.6 Follow-up | After C1, in the same chat: “Of those Economy vehicles, which have a daily rate?” | It narrows the previous set. It does not start a new scan of every track. |
| C4 | W3.8 Filters | “Save a Rentals view of status Reserved only.” | The view exists and lists the reserved rental, or the filter is refused before the card. |

### Do not test

| Package | Why |
| --- | --- |
| W3.4 Multi-hop | Not built. Behind D2. The refusal on the service question is the pass. |
| W3.7 Scale | Not built. No 50k fixture. |
| W4.1–W4.3 | Confirmed above. |
| W4.4 | Confirmed for the Compact rename. Merge and split stage a preview and refuse when a field would be dropped. Those two were not blessed on Vehicles, Customers, or Rentals. |
| W4.5 | Confirmed. One suggestion, one card, on the Vehicles draft. |
| W5.1–W5.4 | Confirmed on the Daily rates dashboard. The tile total is the aggregate, and the tile lists the two vehicles. `table_widget` and `progress` are in both registries. |
| W6.1 | In code. Follow-up is `integral_insights`. Correction, rejection, and resume are `integral_scaffold`. |
| W6.2 | Confirmed once. An open batch with one op was cleared from memory, restored, and still had that one op. |
| W6.3 | In the web source. Settings names the skills and tools for this workspace. Mission Control names the pending approval count. A private skill the caller cannot use is already omitted from the skill list. |
| W6.4 | Confirmed as unavailable. `integral_workspace_setup` and `integral_onboard_user` stay `status: gap`. |
| D1, D2, D3 | Not built. |

---

## Full package list

### Wave 0 — make today’s tools tell the truth

| Package | Status |
| --- | --- |
| W0.1 Action audit | In code. No screen object. Suite: `backend/tests/test_resident_skill_runtime_alignment.py`. |
| W0.2 Proposal diffing | Confirmed for the live correction. “Service logs only” removed `extra_board`. It did not start a new design. |
| W0.3 Eval corpus | In code. No screen object. Suites: `backend/tests/test_live_model_qualification_runner.py`, `backend/tests/test_live_model_qualification_compiler.py`. |
| W0.4 Dependency inventory | In code. No screen object. Suite: `backend/tests/test_capability_map.py`. |

### Wave 1 — flagship App design

| Package | Status |
| --- | --- |
| W1.1 Tags in the build | Confirmed |
| W1.2 Anchors in the build | Confirmed. Toyota Corolla and Honda Civic each have a Service Log track. |
| W1.3 Structured blueprint | Confirmed for seeds and tags. Views: the saved Rentals board is kanban with a Reserved column. A new build that asks for `board` has not been re-run since the alias landed. |
| W1.4 Coverage check | Confirmed. The live map was refused before a card (`n.ChatThread.03754b2ebc5b4c29b276904d`). Automatic texts replied that a trusted package is required and staged nothing (`n.ChatThread.0f466760b92640208576a00d`). |
| W1.5 Build verification | Confirmed for the board. Opened `n.View.93da77a4ae514416b16e4b1a`. Columns are Reserved, Out, and Returned. The 17:59 verify `reply` sentence has not been re-proven in a new chat. |
| W1.6 App-skill discovery | No skill to offer. Car Rental Desk has no private skill. None was invented. |
| W1.7 Custom operation bridge | Phase A confirmed. The reply says the add-on is specified and not installed. Phase B is Not built (D3). |

### Wave 2 — intelligent capture

| Package | Status |
| --- | --- |
| W2.1 Destination ranking | Confirmed for the two-filing prompt (Sam Patel, Honda Civic). |
| W2.2 Duplicate and link resolution | Confirmed after the update and delete tools were pinned. Jane Doe `n.Entry.4d7c34d536604109843271ee` phone is 555-9999. Customers are still Jane Doe and Sam Patel (`n.ChatThread.8ff5be14b7ea40f89aaba89a`). Sandy’s October 4th appointment remains. The October 1st duplicate was deleted. No new appointment is titled with the cleanup sentence (`n.ChatThread.303c3d0c331e48179759c71a`). A later create titled “Sandy Singh Appointment” was approved before the name check existed. A create that shares a name with a row already on the track is now refused before a card. The October 4th row’s `date` is `2026-10-01` after `n.ChatThread.8fcf3cd056db4c549be66727`. A date left only in the title is refused before a card. |
| W2.3 No-fit route | Confirmed |
| W2.4 Domain-skill precedence | Confirmed for the not-file half. `n.ChatThread.ff66ee5a080b48e08dc8665a` did not file Priya Shah. No hire skill is installed in this workspace, so none was offered. It asked whether to file a basic entry. |

### Wave 3 — query and graph inference

| Package | Status |
| --- | --- |
| W3.0 Query boundary | In code. Packaged payroll still speaks an empty generic query as “nothing is set up.” That fix is a separate plan, not this list. |
| W3.1 Aggregation engine | Confirmed for one vehicle’s total and average, and for tag counts. `n.ChatThread.eb635c16980b40d38af4b222` replied “Economy 1, SUV 0, Van 0.” |
| W3.2 Business sort and ranking | Confirmed |
| W3.3 Relations both ways | Confirmed. With the Jane Doe rental open (`n.ChatThread.bb942290664d46c583321f59`) the reply names Jane Doe and the Toyota Corolla. |
| W3.4 Multi-hop traversal | Not built. The live refusal is Confirmed. |
| W3.5 Query planner | Confirmed for the due-back date. |
| W3.6 Chainable results | Confirmed. The follow-up named the Toyota Corolla at daily rate 12000. |
| W3.7 Scale | Not built |
| W3.8 Filter contract parity | Confirmed. View `n.View.f00e2d6ae7b646d0a87149f5` “Reserved Rentals” filters `custom_fields.status` equals Reserved. |
| W3.9 Field-key contract | Confirmed. Toyota Corolla `n.Entry.b07950d674fa4577bd942925` `daily_rate` is 12000 after the label “Daily rate”. |

### Wave 4 — substrate evolution

| Package | Status |
| --- | --- |
| W4.1 Migration-emitting patch ops | Confirmed for rename. Customers field key is `mobile`. Jane Doe and Sam Patel’s numbers moved with it. |
| W4.2 Field-level `modify_model` | Decided. Field edits stay on the draft. A key change is `rename_field`. `modify_field` refuses a key change. `integral_modify_model` still has no field actions. |
| W4.3 Bulk move | Confirmed for the named refusal. The Jane Doe rental stayed on Rentals. Maintenance has no field for customer, due_back_date, start_date, status, or vehicle. |
| W4.4 Track and tag restructuring | Confirmed for the rename. Economy is Compact. Toyota Corolla still has the tag. `integral_merge_tags`, `integral_merge_tracks`, and `integral_split_track` stage a preview and refuse a move that would drop a field. |
| W4.5 Contextual improvement | Confirmed. One Vehicles suggestion, the Daily Rate table, is on draft `n.OperationalModel.9f5bd5f682274f1d889d5065`. |

### Wave 5 — dashboards

| Package | Status |
| --- | --- |
| W5.1 Aggregate data sources | Confirmed. Daily rates `kind: aggregate` sums `daily_rate` to 12080. |
| W5.2 Schema-aware suggester | Confirmed for this board. The tile rationale says the number field is a sum. `suggest_dashboard_template` reads each track’s fields the same way. |
| W5.3 Widget palette additions | In code. `metric_card` and `chart_line` were already registered. `table_widget` and `progress` are now in the backend registry, the frontend registry, and the view contracts. |
| W5.4 Drill-through | Confirmed. The tile lists Toyota Corolla at 12000 and Honda Civic at 80, which add up to the total. |

### Wave 6 — reliability and experience

| Package | Status |
| --- | --- |
| W6.1 Skill ownership and routing | In code. One owner per routing case, recorded in the qualification manifest. |
| W6.2 Durable open batches | Confirmed once. Restoring after the in-memory batch was cleared returned the same single op. A stored batch whose revision does not match is dropped. |
| W6.3 Visibility | In the web source. Settings states which skills and tools this workspace can use. Mission Control shows the pending approval count. Private skills the caller cannot open stay off the list. |
| W6.4 Deferred shortcuts | Confirmed as unavailable. Both shortcuts remain `status: gap` and are not advertised as working. |

### Decision gates

| Gate | Status |
| --- | --- |
| D1 App → library package | Not built |
| D2 QuerySpec v2 multi-hop | Not built |
| D3 Resident-generated executable tools | Not built |
| D4 `integral_modify_model` field path | Not built. Blocks W4.2 only. |
