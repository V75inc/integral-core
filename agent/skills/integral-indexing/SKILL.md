---
name: integral-indexing
description: Inspect search-index readiness and maintain the index of an existing list. Configure which canonical text fields are searchable, exclude fields, select entry types, request a rebuild and inspect progress. Indexing never grants access. Use integral-insights to answer questions from existing content.
allowed-tools: integral_get_index_status integral_configure_search_index integral_reindex_track integral_list_tracks integral_get_track_schema
---

# Search indexing

1. Resolve the exact existing Track in the active workspace. Read its schema
   and `integral_get_index_status`. A caller must have current configuration
   authority on that Track. A declaration cannot make inaccessible content
   visible or bypass an App's governed-query boundary.
2. Prefer the existing declaration. Indexing runs automatically for eligible
   entries, with background reconciliation for older/imported/installed data.
   A missing or failed index is not evidence that the source contains no answer.
3. To change selection, propose `integral_configure_search_index` with the
   Track id and a complete `definition`. Supported properties:
   `enabled`, `fields`, `exclude_fields`, `entry_type_ids`, `metadata_fields`.
   Text paths are `title`, `body`, `custom_fields.<key>` and `custom_fields.*`.
   Never select credentials, private operational metadata or fields the user
   has asked to exclude. This proposes a change; wait for ordinary approval.
4. To rebuild without changing selection, propose `integral_reindex_track`.
   Wait for approval, then inspect status. Rebuild requested/queued is not the
   same as indexed or complete. Report retry/failure/pending state plainly.
5. Index writes and search queries use the deployment's versioned embedding
   profile. Do not invent a model/provider or transmit content elsewhere.
   A model change needs a separate profile/backfill and operator configuration.
6. Keep responses short. Say what is ready and what is still indexing; do not
   claim full coverage from job counts alone.
