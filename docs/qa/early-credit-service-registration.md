# Early-credit trial service registration

The first live trial after PR #128 found that the documented
`configure_early_credit_delivery` service had its schema, administrator guard
and coordinator dispatch implemented, but was absent from `const.SERVICES`.
Home Assistant therefore did not register it. The enable request failed;
the live policy remained disabled and no early-credit test payment was sent.

The correction adds that service to the registration list. It does not enable
the experiment, grant payment authority, change recipients or retry a payment.
Regression checks now require all documented services to be registered
(including the separately installed grouped-wallet diagnostic), and require
the early-credit service to exist after actual Home Assistant setup.

Deployment acceptance must check service discovery, not just shipped file
hashes and a disabled policy field. An authorised trial still needs explicit
enablement, one new credit, an original-wallet receipt report before mining,
and confirmation of the same transaction without a replacement.
