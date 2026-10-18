# Profile maintenance

- This repository renders the GitHub profile; `elanthingal.github.io` is a separate site.
- Preserve the existing SVG palette and embedded SG/DMM fonts. Edit `scripts/heartbeat-template.svg` for heartbeat design; `assets/heartbeat.svg` is generated.
- Keep the heartbeat's full contribution history from 2016, group across year boundaries, and exclude future dates. Missing API data must fail without replacing the last successful image.
- Run `python3 -m unittest discover -s tests -v` after changing the generator, template, README links, or workflow.
- Validate remote badge images through GitHub's rendered image proxy; a direct HTTP 200 does not prove the profile image works.
