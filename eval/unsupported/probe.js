function chargeCard(token, amount) {
  return fetch("/charge", {method: "POST", body: JSON.stringify({token, amount})});
}
