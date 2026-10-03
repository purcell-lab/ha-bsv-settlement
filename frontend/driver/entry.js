// Keep existing private invitation and public-enrolment flows intact.
const fragment=new URLSearchParams(location.hash.slice(1));
if((fragment.has("budget")&&fragment.has("token"))||(fragment.has("join")&&fragment.has("key")))
  await import("./app.js");
else await import("./portal.js");
