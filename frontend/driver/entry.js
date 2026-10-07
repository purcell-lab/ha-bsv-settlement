// Retain legacy private links; public registration now uses the unified portal.
const fragment=new URLSearchParams(location.hash.slice(1));
if(fragment.has("budget")&&fragment.has("token"))
  await import("./app.js");
else await import("./portal.js");
