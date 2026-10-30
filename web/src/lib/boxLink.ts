export const BOX_LINK_MESSAGE = "Use a Duke Box shared link that starts with https://duke.box.com/s/";

const BOX_LINK = /^https:\/\/duke(\.app)?\.box\.com\/s\/[A-Za-z0-9]{10,64}\/?([?#]\S*)?$/;

/** null when the link is empty or acceptable, else the message to show. */
export function boxLinkProblem(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  return BOX_LINK.test(v) ? null : BOX_LINK_MESSAGE;
}
