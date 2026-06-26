import requests

html = requests.get("https://www.karlancer.com/projects/اپلیکیشن-فرمساز-آنلاین-vuejs-906nm97no8ew").text

with open("project.html", "w", encoding="utf-8") as f:
    f.write(html)