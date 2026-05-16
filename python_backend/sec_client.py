import os
import re
import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from lxml import etree
import tempfile


class RateLimited(Exception):
    pass

class XmlUrlsNotFound(Exception):
    pass

class SecClient:
    BASE_URL = "https://www.sec.gov"
    EXPECTED_COL_NAMES = ["cik", "company_name", "form_type", "date_filed", "filename"]
    THIRTEEN_F_FORM_TYPES = ["13F-HR", "13F-HR/A"]

    def __init__(self):
        self.user_agent = os.environ.get("SEC_USER_AGENT", "Sample Company Name AdminContact@example.com")

    def request_headers(self):
        return {"User-Agent": self.user_agent}

    def padded_cik(self, cik_val):
        return str(cik_val).strip().rjust(10, "0")

    def get(self, url):
        response = requests.get(url, headers=self.request_headers())
        if response.status_code == 429:
            raise RateLimited()
        response.raise_for_status()
        return response

    def _parse_float(self, value):
        if value is None:
            return None
        v = value.strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def thirteen_f_filings(self, filing_year, filing_quarter, delete_tmpfile=True):
        url = f"{self.BASE_URL}/Archives/edgar/full-index/{filing_year}/QTR{filing_quarter}/master.idx"

        response = self.get(url)
        content = response.text.split("\n")

        thirteen_fs = []
        col_names = None

        for raw_line in content:
            line = [x.strip() for x in raw_line.strip().split("|")]

            if len(line) != len(self.EXPECTED_COL_NAMES):
                continue

            if col_names is None:
                col_names = [n.lower().replace(" ", "_") for n in line]
                if col_names != self.EXPECTED_COL_NAMES:
                    raise Exception("Unexpected column names")
                continue

            row = dict(zip(col_names, line))

            if row["form_type"] not in self.THIRTEEN_F_FORM_TYPES:
                continue

            full_submission_url = f"{self.BASE_URL}/Archives/{row['filename']}"
            dir_url = full_submission_url.replace(".txt", "").replace("-", "")

            thirteen_fs.append({
                "external_id": dir_url.split("/")[-1],
                "company_name": row["company_name"],
                "form_type": row["form_type"].upper(),
                "cik": self.padded_cik(row["cik"]),
                "date_filed": datetime.datetime.strptime(row["date_filed"], "%Y-%m-%d").date(),
                "full_submission_url": full_submission_url,
                "directory_url": dir_url
            })

        return thirteen_fs

    def latest_thirteen_f_filings(self, filed_since=None, per_page=100, max_pages=100):
        if filed_since is None:
            filed_since = datetime.date.today() - datetime.timedelta(days=1)

        url = f"{self.BASE_URL}/cgi-bin/browse-edgar"

        query_params = {
            "action": "getcurrent",
            "count": per_page,
            "output": "atom",
            "type": "13F-HR",
            "start": 0
        }

        title_regex = re.compile(r"^13F-HR(?:/A)? - (.+?) \((\d{10})\)")
        results = []

        for _ in range(max_pages):
            response = requests.get(url, params=query_params, headers=self.request_headers())
            if response.status_code == 429:
                raise RateLimited()

            soup = BeautifulSoup(response.content, 'xml')
            entries = soup.find_all("entry")

            for e in entries:
                date_filed_str = e.find("updated").text
                date_filed = datetime.datetime.fromisoformat(date_filed_str.split("T")[0]).date()

                if date_filed < filed_since:
                    continue

                link_tag = e.find("link")
                directory_url = "/".join(link_tag["href"].split("/")[:-1])
                external_id = directory_url.split("/")[-1]
                cik = self.padded_cik(directory_url.split("/")[-2])

                category_tag = e.find("category", label="form type")
                form_type = category_tag["term"] if category_tag else None

                title = e.find("title").text
                match = title_regex.search(title)
                company_name = match.group(1) if match else title

                results.append({
                    "external_id": external_id,
                    "company_name": company_name,
                    "form_type": form_type,
                    "cik": cik,
                    "date_filed": date_filed,
                    "directory_url": directory_url
                })

            if len(entries) < per_page:
                break

            query_params["start"] += per_page

        return results

    def _text_squish(self, node):
        if node is None or node.text is None:
            return None
        return re.sub(r'\s+', ' ', node.text).strip()

    def parse_primary_doc_xml(self, xml_content):
        # Remove namespaces using regex to make parsing easier (equivalent to doc.remove_namespaces! in Ruby)
        if isinstance(xml_content, str):
            xml_content = xml_content.encode('utf-8')
        xml_content_no_ns = re.sub(rb' xmlns="[^"]+"', b'', xml_content)
        soup = BeautifulSoup(xml_content_no_ns, 'xml')

        report_calendar_node = soup.find("reportCalendarOrQuarter")
        date_string = self._text_squish(report_calendar_node)
        report_date = datetime.datetime.strptime(date_string, "%m-%d-%Y").date() if date_string else None

        other_managers = []
        for o in soup.find_all("otherManager2"):
            seq_num_node = o.find("sequenceNumber")
            file_num_node = o.find("form13FFileNumber")
            name_node = o.find("name")

            other_managers.append({
                "sequence_number": int(seq_num_node.text) if seq_num_node and seq_num_node.text else None,
                "file_number": self._text_squish(file_num_node),
                "name": self._text_squish(name_node)
            })

        def get_text_lower(node_name, parent=soup):
            node = parent.find(node_name)
            t = self._text_squish(node)
            return t.lower() if t else None

        def get_text_upper(node_name, parent=soup):
            node = parent.find(node_name)
            t = self._text_squish(node)
            return t.upper() if t else None

        address_node = soup.find("address") or soup

        return {
            "report_date": report_date,
            "street1": get_text_lower("street1", address_node),
            "street2": get_text_lower("street2", address_node),
            "city": get_text_lower("city", address_node),
            "state_or_country": get_text_upper("stateOrCountry", address_node),
            "zip_code": self._text_squish(address_node.find("zipCode")),
            "other_included_managers_count": self._text_squish(soup.find("otherIncludedManagersCount")),
            "holdings_count_reported": self._text_squish(soup.find("tableEntryTotal")),
            "holdings_value_reported": self._text_squish(soup.find("tableValueTotal")),
            "confidential_omitted": self._text_squish(soup.find("isConfidentialOmitted")),
            "report_type": get_text_lower("reportType"),
            "amendment_type": get_text_lower("amendmentType"),
            "amendment_number": int(self._text_squish(soup.find("amendmentNo"))) if soup.find("amendmentNo") and self._text_squish(soup.find("amendmentNo")) else None,
            "file_number": self._text_squish(soup.find("form13FFileNumber")),
            "other_managers": other_managers
        }

    def parse_info_table_xml(self, xml_content):
        if isinstance(xml_content, str):
            xml_content = xml_content.encode('utf-8')
        xml_content_no_ns = re.sub(rb' xmlns="[^"]+"', b'', xml_content)
        soup = BeautifulSoup(xml_content_no_ns, 'xml')

        holdings = []
        for i in soup.find_all("infoTable"):
            cusip_node = i.find("cusip")
            cusip = self._text_squish(cusip_node).upper().rjust(9, "0") if cusip_node else None

            value_node = i.find("value")

            def get_squished(name):
                return self._text_squish(i.find(name))

            def get_lower(name):
                t = get_squished(name)
                return t.lower() if t else None

            voting_sole = i.find("Sole") # Inside votingAuthority
            voting_shared = i.find("Shared")
            voting_none = i.find("None")
            if not voting_sole:
                va = i.find("votingAuthority")
                if va:
                    voting_sole = va.find("Sole")
                    voting_shared = va.find("Shared")
                    voting_none = va.find("None")

            holdings.append({
                "cusip": cusip,
                "issuer_name": get_squished("nameOfIssuer"),
                "class_title": get_lower("titleOfClass"),
                "value": self._parse_float(self._text_squish(value_node)),
                "shares_or_principal_amount": get_squished("sshPrnamt"),
                "shares_or_principal_amount_type": get_lower("sshPrnamtType"),
                "option_type": get_lower("putCall"),
                "investment_discretion": get_lower("investmentDiscretion"),
                "other_manager": get_squished("otherManager"),
                "voting_authority_sole": self._text_squish(voting_sole) if voting_sole else None,
                "voting_authority_shared": self._text_squish(voting_shared) if voting_shared else None,
                "voting_authority_none": self._text_squish(voting_none) if voting_none else None
            })

        return holdings

    def xml_urls(self, directory_url):
        response = self.get(directory_url)
        soup = BeautifulSoup(response.content, 'html.parser')

        urls = []
        for a in soup.select("#main-content a"):
            href = a.get("href")
            if href and href.lower().endswith(".xml"):
                urls.append(urljoin(self.BASE_URL, href))

        if not urls:
            raise XmlUrlsNotFound()

        return urls

    def primary_doc_url(self, xml_urls):
        for url in xml_urls:
            if re.search(r'primary.*doc', url, re.IGNORECASE):
                return url

        for url in xml_urls:
            response = self.get(url)
            if b"edgarSubmission" in response.content:
                return url
        return None

    def info_table_url(self, xml_urls):
        for url in xml_urls:
            if re.search(r'info.*table', url, re.IGNORECASE):
                return url

        for url in xml_urls:
            response = self.get(url)
            xml_content_no_ns = re.sub(rb' xmlns="[^"]+"', b'', response.content)
            if b"<informationTable" in xml_content_no_ns:
                return url
        return None

client = SecClient()
