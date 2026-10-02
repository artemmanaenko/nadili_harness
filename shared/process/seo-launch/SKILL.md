---
name: seo-launch
description: Activate or reactivate Nadili public-site search discovery through its existing SEO gate, sitemap, Google Search Console, Bing Webmaster Tools and IndexNow. Use for an owner-requested launch or rollback recovery, not recurring audits.
---

# SEO activation

Work in the consuming Nadili checkout and follow its current
`docs/runbooks/search-indexing-activation.md`. That runbook and the deployed
site define the actual flags, commands and rollback; this harness skill is a
reusable activation method, not a record that a particular site was submitted.
Do not run product operations from this portfolio checkout.

1. Confirm the intended production site, published content and public locales.
   Check the disabled discovery contract before enabling search discovery.
   Already published Answers do not need a second editorial publication.
2. Confirm site ownership in Google Search Console and Bing Webmaster Tools.
   Use browser control when the owner asks. Stop for the owner's sign-in,
   verification challenge or credential when required.
3. Enable the documented runtime gate, allow propagation and purge only the
   documented discovery URLs from edge cache. Check robots, sitemap children,
   canonical metadata and URL counts by locale and content type against the
   intended scope. With one public locale, use the runbook's manual checks if
   the live checker cannot exercise a real fallback Answer URL.
4. Submit the root sitemap index to Google and Bing. Record submission,
   fetch/processing and indexing separately. If a console says it cannot fetch,
   inspect a live fetch, XML and edge security events; change crawler rules only
   on evidence and retry within the runbook's boundary.
5. Check the public IndexNow key file and run the documented dry run. Investigate
   unexpected removals, submit the normal diff, then dry-run again for content
   added during submission. Use a full resubmission only after the owner approves
   reviewed counts.
6. Report each service's observed state and any pending processing. Sitemap or
   IndexNow acceptance does not establish indexing, ranking or traffic.

The Admin **Update index** action rebuilds internal search; it does not enable
public SEO. Never invent a fallback URL, disclose credentials or copy private
console/account data into repository files. Keep recurring monitoring in a
separate skill or scheduled workflow.
