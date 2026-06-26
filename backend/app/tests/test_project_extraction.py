import requests
from bs4 import BeautifulSoup
url ="https://www.karlancer.com/projects/اپلیکیشن-فرمساز-آنلاین-vuejs-906nm97no8ew"
html = requests.get(url)

soup = BeautifulSoup(html.text, "html.parser")

print(html.status_code)
print(html.text[:1000])

title = soup.select_one("h1.fs-18").get_text(" ", strip=True)
days = soup.select_one("div.mt-3:nth-child(3) > div:nth-child(1)").get_text(" ", strip=True)
print(title)
