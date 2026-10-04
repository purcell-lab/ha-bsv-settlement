// Identifiers only: never display a capability token as a session reference.
export function sessionReference(terms,binding,selectedSession){
  if(!terms)return null;
  const multi=terms.version===3;
  const session=multi?(selectedSession||terms.included_session?.session_id):binding?.session_id||
    (terms.session_mode==="existing_session"?terms.session_id:null);
  return {
    approval:terms.budget_id,
    session:session||(multi?"No session selected yet":"Not assigned"),
    transaction:multi?(terms.included_session&&session===terms.included_session.session_id?terms.included_session.transaction_id:session||"Not assigned"):
      binding?.transaction_id||(session?terms.transaction_id:"Not assigned"),
    scope:multi?(terms.included_session?"Current and future sessions":"Multiple future sessions"):
      terms.closed_session_review?"This completed session only":"One session only",
  };
}
