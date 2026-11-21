import type { ModelProfile } from "../../../api/aiConfiguration";

/** Conservative catalog classification, not a claim of provider capability.
 * Older profiles fall back to known model names; custom IDs remain configurable
 * through embedding settings and must pass the provider test.
 */
export function isEmbeddingProfile(profile: Pick<ModelProfile, "model" | "harness_policy">): boolean {
  if (profile.harness_policy?.tier === "embedding") return true;
  const id = profile.model.trim().toLowerCase().split("/").pop() ?? "";
  return /^(?:text-embedding|embeddinggemma|nomic-embed|all-minilm|mxbai-embed|embed-)/.test(id);
}
