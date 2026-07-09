#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
pandoc slideshow.md -t beamer --pdf-engine=xelatex -L mermaid.lua \
  -V theme=metropolis \
  -V colortheme=default \
  -V fontsize=12pt \
  -V aspectratio=169 \
  -o slideshow.pdf
echo "slideshow.pdf"
