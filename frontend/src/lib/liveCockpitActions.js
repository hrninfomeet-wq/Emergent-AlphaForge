/**
 * Stand the manual order path down and make failure impossible to swallow.
 *
 * Kept outside JSX so the consequential async branch is executable through
 * Node. The caller owns presentation (toast/banner), while this helper owns the
 * guarantee that a rejected mode write reaches it and state is refreshed either
 * way.
 */
export async function standDownManualExecution({ setMode, onFailure, onSettled }) {
  try {
    await setMode("LIVE_OFFLINE");
    return true;
  } catch (error) {
    onFailure?.(error);
    return false;
  } finally {
    onSettled?.();
  }
}

/** Resolve the broker chip state from both token-expiry signals. */
export function brokerConnectionState(status) {
  const expired = Boolean(status?.expired || status?.regenerate_after_6am);
  const connected = Boolean(status?.connected) && !expired;
  return {
    connected,
    expired,
    stateLabel: connected ? "connected" : expired ? "token expired" : "disconnected",
  };
}

/** Classify a resolved placement response without mistaking lost ACK for reject. */
export function placementOutcome(result) {
  return {
    placed: Boolean(result?.placed),
    unconfirmed: Boolean(result?.indeterminate),
    detail: result?.reason || "broker acknowledgement unknown",
  };
}
