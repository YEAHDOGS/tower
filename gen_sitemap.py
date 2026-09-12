#!/usr/bin/env python3
"""Generate sitemap.xml + robots.txt for the Watchtower static site.

Scans projects/*/index.html (project detail pages) plus the hand-maintained
hub index.html and writes a crawler sitemap with lastmod from file mtimes.
robots.txt points crawlers at the sitemap. No network, no invented data.

Usage: python3 gen_sitemap.py
"""
import datetime
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://yeahdogs.github.io/tower/"
PROJECTS = os.path.join(HERE, "projects")

def urls():
    out = [("index.html", "1.0")]  # hub
    for slug in sorted(os.listdir(PROJECTS)):
        page = os.path.join(PROJECTS, slug, "index.html")
        if os.path.isfile(page):
            out.append((os.path.join("projects", slug, "index.html"), "0.8"))
    return out

def main():
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for rel, priority in urls():
        path = os.path.join(HERE, rel)
        lastmod = datetime.datetime.fromtimestamp(
            os.path.getmtime(path), tz=datetime.timezone.utc
        ).date().isoformat()
        loc = BASE + rel.replace("index.html", "")
        url = ET.SubElement(urlset, "url")
        ET.SubElement(url, "loc").text = loc
        ET.SubElement(url, "lastmod").text = lastmod
        ET.SubElement(url, "priority").text = priority
    tree = ET.ElementTree(urlset)
    ET.indent(tree)
    sitemap = os.path.join(HERE, "sitemap.xml")
    tree.write(sitemap, encoding="utf-8", xml_declaration=True)
    with open(os.path.join(HERE, "robots.txt"), "w") as f:
        f.write("User-agent: *\nAllow: /\n\nSitemap: " + BASE + "sitemap.xml\n")
    print("wrote", sitemap, "with", len(urlset), "urls")

if __name__ == "__main__":
    main()
