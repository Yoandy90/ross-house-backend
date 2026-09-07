# Ross House runtime business boundary

The service fails startup in deployed environments unless the database name is
exactly ross_house_production or ross_house_staging for its environment.
Local development defaults to ross_house_local. Historical taxportal, Ross Tax,
Ross Lending, loan/lending and unrelated database namespaces are rejected.

Deployed CORS permits only rosshouserentals.com and its www host. Wildcard CORS
exists only for an explicitly local development environment. Ross Lending and
external preview hosts are not trusted origins for this service.

The vault handles only the current Ross House format with encrypted routing and
account fields. Records containing legacy plaintext, old card ciphertext or NMI
loan fields are omitted from lists and blocked from reveal and deletion. This is
intentional fail-closed behavior: do not add a legacy key or copy those values.
Ownership must be established through the separate offline evidence workflow.

The real-estate Oportunidades module remains in scope. Radar, obituaries,
struck-off property requests and deal-finder workers are Ross House acquisition
features and remain registered. References to county tax offices and property
taxes are real-estate operations, not Ross Tax Preparation.

The admin-only staging readiness endpoint reports only booleans and fixed issue
codes. It requires the exact staging database, disabled background work, strong
tenant sessions, and presence of the vault encryption key. It never returns
secret values and performs no configuration changes or deployment.
