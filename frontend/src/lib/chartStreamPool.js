const channelId = (key, timeframe) => JSON.stringify([key, timeframe]);
const channelEvents = ["snapshot", "heartbeat", "resync", "stream_error"];

// A page uses one HTTP connection for all charts, leaving browser connection
// slots available for history, positions and the rest of the application.
export function createChartStreamPool(api, Source) {
  const channels = new Map();
  let connection = null, activeSignature = "", scheduled = false;

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    Promise.resolve().then(() => { scheduled = false; reconnect(); });
  }

  function reconnect() {
    const selected = [...channels.values()].map(({ key, timeframe }) => ({ instrument_key: key, timeframe }))
      .sort((a, b) => channelId(a.instrument_key, a.timeframe).localeCompare(channelId(b.instrument_key, b.timeframe)));
    const signature = JSON.stringify(selected);
    if (signature === activeSignature && connection) return;
    if (connection) connection.close();
    connection = null;
    activeSignature = signature;
    if (!selected.length) return;
    channels.forEach(channel => channel.consumers.forEach(consumer => { consumer.readyState = 0; }));
    const EventSourceClass = Source || globalThis.EventSource;
    let source;
    try {
      source = new EventSourceClass(`${api}/charts/streams?${new URLSearchParams({ channels: signature })}`);
    } catch {
      activeSignature = ""; // A subsequent registration can retry this same set.
      channels.forEach(channel => [...channel.consumers].forEach(consumer => consumer.dispatch("error", new Event("error"))));
      return;
    }
    connection = source;
    for (const type of channelEvents) {
      source.addEventListener(type, event => {
        if (connection !== source) return;
        let payload;
        try { payload = JSON.parse(event.data); } catch { return; }
        const channel = channels.get(channelId(payload?.instrument_key, payload?.timeframe));
        [...(channel?.consumers || [])].forEach(consumer => consumer.dispatch(type, event));
      });
    }
    for (const type of ["open", "error"]) {
      source.addEventListener(type, event => {
        if (connection !== source) return;
        const consumers = [...channels.values()].flatMap(channel => [...channel.consumers]);
        consumers.forEach(consumer => {
          consumer.readyState = type === "open" ? 1 : 0;
          consumer.dispatch(type, event);
        });
      });
    }
  }

  return {
    stream(key, timeframe) {
      const id = channelId(key, timeframe);
      let channel = channels.get(id);
      if (!channel) {
        if (channels.size >= 6) throw new RangeError("At most six distinct chart channels are supported");
        channel = { key, timeframe, consumers: new Set() };
        channels.set(id, channel);
      }
      const listeners = new Map();
      let closed = false;
      const consumer = {
        onopen: null, onerror: null, readyState: 0,
        addEventListener(type, callback) {
          if (!callback || closed) return;
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(callback);
        },
        removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
        dispatch(type, event) {
          if (closed) return;
          const invoke = callback => {
            // Match EventTarget: one failing listener must not starve siblings.
            try {
              if (typeof callback === "function") callback.call(consumer, event);
              else callback?.handleEvent(event);
            } catch (error) { globalThis.reportError?.(error); }
          };
          [...(listeners.get(type) || [])].forEach(invoke);
          if (typeof consumer[`on${type}`] === "function") invoke(consumer[`on${type}`]);
        },
        close() {
          if (closed) return;
          closed = true; consumer.readyState = 2; listeners.clear();
          channel.consumers.delete(consumer);
          if (!channel.consumers.size) channels.delete(id);
          if (!channels.size) {
            connection?.close(); connection = null; activeSignature = "";
          }
          schedule();
        },
      };
      channel.consumers.add(consumer);
      schedule();
      const joinedConnection = connection;
      if (joinedConnection?.readyState === 1) Promise.resolve().then(() => {
        if (!closed && connection === joinedConnection && consumer.readyState === 0) {
          consumer.readyState = 1;
          consumer.dispatch("open", new Event("open"));
        }
      });
      return consumer;
    },
  };
}
