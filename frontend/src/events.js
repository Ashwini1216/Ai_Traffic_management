// Small app-wide events between components that share no state, such as a place search and
// the map beside it, or journey monitoring and the route map.
export function publish(name, detail) {
  window.dispatchEvent(new CustomEvent(`flowstate:${name}`, { detail }));
}

export function subscribe(name, handler) {
  const listener = event => handler(event.detail);
  window.addEventListener(`flowstate:${name}`, listener);
  return () => window.removeEventListener(`flowstate:${name}`, listener);
}
