import re
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
from bs4 import BeautifulSoup
import aiohttp

from parser_utils import parse_eur, lv_time_now

# Normalized fuel keys we support
SUPPORTED_FUELS = {
    "a95", "a98", "diesel", "lpg"
}

FUEL_ALIASES = {
    "a95": {"a95", "95", "95e", "95miles", "futura 95", "a-95", "eurosuper 95"},
    "a98": {"a98", "98", "98e", "98miles", "futura 98", "a-98", "eurosuper 98"},
    "diesel": {"diesel", "d", "dīzelis", "d-miles", "dmiles", "futura d", "miles diesel", "dīzeļdegviela"},
    "lpg": {"lpg", "autogāze", "auto gāze", "autogas", "propāns-butāns"}
}

def normalize_fuel_type(s: str) -> Optional[str]:
    s = (s or "").strip().lower()
    for key, aliases in FUEL_ALIASES.items():
        if s in aliases:
            return key
    # loose mapping
    if s in {"95e", "95m", "a95e"}:
        return "a95"
    if s in {"98e", "98m", "a98e"}:
        return "a98"
    return None if s not in SUPPORTED_FUELS else s

class FuelFetcher:
    """
    Fetch prices from several public sources.
    Each station dict:
      {
        "name": str,
        "address": str,
        "prices": {"a95": float?, "a98": float?, "diesel": float?, "lpg": float?},
        "source": "gas.didnt.work" | "circlek" | "neste" | "virsi" | "viada",
        "timestamp": "YYYY-MM-DD" or ""
      }
    """
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def fetch_all(self) -> List[Dict[str, Any]]:
        results = await asyncio.gather(
            self.fetch_gas_didnt_work(),
            self.fetch_circlek(),
            self.fetch_neste(),
            self.fetch_virsi(),
            self.fetch_viada(),
            return_exceptions=True
        )
        stations: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, Exception):
                continue
            stations.extend(res)
        # Deduplicate by (name,address)
        uniq: Dict[tuple, Dict[str, Any]] = {}
        for st in stations:
            key = (st.get("name","").strip().lower(), st.get("address","").strip().lower())
            if key in uniq:
                # merge prices
                for k,v in st.get("prices", {}).items():
                    if v is not None:
                        # keep lower price if conflict
                        old = uniq[key]["prices"].get(k)
                        if old is None or (isinstance(v,(int,float)) and v < old):
                            uniq[key]["prices"][k] = v
            else:
                uniq[key] = st
        return list(uniq.values())

    async def _get(self, url: str) -> str:
        async with self.session.get(url, headers={"User-Agent":"Mozilla/5.0"}) as r:
            r.raise_for_status()
            return await r.text()

    async def fetch_gas_didnt_work(self) -> List[Dict[str, Any]]:
        # Community map with Waze-reported prices for Baltics & Poland.
        # The page includes text nodes like: "Viada Valgales Valgales iela, 1, Rīga, Latvia, 1.37 €/l. 2025-11-08"
        url = "https://gas.didnt.work/"
        try:
            html = await self._get(url)
        except Exception:
            return []
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        # Pattern: "<chain> <station name/address>, <price> €/l. <date>"
        # We'll capture sequences ending with '€/l.' and an ISO-like date nearby.
        pattern = re.compile(r"([A-ZĀČĒĢĪĶĻŅŌŖŠŪŽa-z0-9 .,'\"\-]+?),\s*([0-9]+(?:\.[0-9]{2,3})?)\s*€/?l\.?\s*([0-9]{4}\-[0-9]{2}\-[0-9]{2})?")
        stations: List[Dict[str, Any]] = []
        for m in pattern.finditer(text):
            full = m.group(1).strip()
            price = parse_eur(m.group(2))
            date = m.group(3) or ""
            # Heuristic split: last comma separates address start
            parts = [p.strip() for p in full.split(",")]
            name = parts[0]
            address = ", ".join(parts[1:]) if len(parts) > 1 else ""
            # We don't know which fuel type the price is for; Waze usually reports A95 or Diesel.
            # We'll map this price to all fuels as a candidate, letting chain-specific pages refine later.
            prices = {"a95": price, "diesel": price}
            stations.append({
                "name": name,
                "address": address,
                "prices": prices,
                "source": "gas.didnt.work",
                "timestamp": date
            })
        return stations

    async def fetch_circlek(self) -> List[Dict[str, Any]]:
        url = "https://www.circlek.lv/degviela-miles/degvielas-cenas"
        try:
            html = await self._get(url)
        except Exception:
            return []
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        # Extract lines like: "95miles 1.514 EUR Anniņmuižas bulvāris 25a, Gunāra Astras iela 10, ..."
        prices = {}
        def grab(label, key):
            idx = text.find(label)
            if idx == -1: return
            seg = text[idx: idx+200]  # small window
            m = re.search(r"([0-9]+\.[0-9]{3})\s*EUR", seg)
            if m:
                prices[key] = parse_eur(m.group(1))
        grab("95miles", "a95")
        grab("98miles", "a98")
        grab("Dmiles,", "diesel")
        grab("Autogāze", "lpg")

        # Addresses - rough extraction
        stations: List[Dict[str, Any]] = []
        addr_match = re.search(r"95miles.*?EUR\s+(.*?)\s+98miles", text)
        addresses = []
        if addr_match:
            addrs = addr_match.group(1)
            addresses = [a.strip() for a in addrs.split(",") if len(a.strip())>3]
        for addr in addresses[:20]:
            stations.append({
                "name": "Circle K",
                "address": addr,
                "prices": prices.copy(),
                "source": "circlek",
                "timestamp": ""
            })
        return stations

    async def fetch_neste(self) -> List[Dict[str, Any]]:
        url = "https://www.neste.lv/lv/content/degvielas-cenas"
        try:
            html = await self._get(url)
        except Exception:
            return []
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        prices = {}
        m95 = re.search(r"Futura\s*95\s*([\d]+\.[\d]{3})", text)
        m98 = re.search(r"Futura\s*98\s*([\d]+\.[\d]{3})", text)
        md = re.search(r"Futura\s*D\s*([\d]+\.[\d]{3})", text)
        if m95: prices["a95"] = parse_eur(m95.group(1))
        if m98: prices["a98"] = parse_eur(m98.group(1))
        if md: prices["diesel"] = parse_eur(md.group(1))
        # Addresses chunk after each price table
        stations: List[Dict[str, Any]] = []
        addr_block = []
        addr_match = re.search(r"Futura\s*95.*?(\d\.\d{3}).*?\s+([A-Za-zĀ-ž0-9\.\- ,]+)\s+Neste Futura 98", text)
        if addr_match:
            addr_text = addr_match.group(2)
            addr_block = [a.strip() for a in re.split(r"[\,;]", addr_text) if len(a.strip())>3]
        for addr in addr_block[:20]:
            stations.append({
                "name": "Neste",
                "address": addr,
                "prices": prices.copy(),
                "source": "neste",
                "timestamp": ""
            })
        return stations

    async def fetch_virsi(self) -> List[Dict[str, Any]]:
        url = "https://www.virsi.lv/en/private/fuel/fuel-price"
        try:
            html = await self._get(url)
        except Exception:
            return []
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        prices = {}
        m95 = re.search(r"95E\s*Gasoline\s*([\d]+\.[\d]{3})", text)
        m98 = re.search(r"98E\s*Gasoline\s*([\d]+\.[\d]{3})", text)
        md = re.search(r"Diesel\s*fuel\s*([\d]+\.[\d]{3})", text)
        lpg = re.search(r"LPG\s*([\d]+\.[\d]{3})", text)
        if m95: prices["a95"] = parse_eur(m95.group(1))
        if m98: prices["a98"] = parse_eur(m98.group(1))
        if md: prices["diesel"] = parse_eur(md.group(1))
        if lpg: prices["lpg"] = parse_eur(lpg.group(1))
        # Addresses visible in the page for specific fuels; we add a couple of known examples if present
        addresses = []
        for m in re.finditer(r"(Brivibas|K\u0101r\u013ca Ulma\u0146a|K\u0101r\u013ca|Kārļa Ulma\u0146a|Kārļa Ulmaņa|Krasta)\s+[^\s].{0,60}", text):
            addresses.append(m.group(0))
        stations = []
        for addr in addresses[:10]:
            stations.append({
                "name": "Virši",
                "address": addr,
                "prices": prices.copy(),
                "source": "virsi",
                "timestamp": ""
            })
        return stations

    async def fetch_viada(self) -> List[Dict[str, Any]]:
        url = "https://www.viada.lv/zemakas-degvielas-cenas/"
        try:
            html = await self._get(url)
        except Exception:
            return []
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        prices = {}
        # Grab one or more price tokens like "1.522 EUR" and "0.735 EUR" and map heuristically
        for m in re.finditer(r"([0-9]+\.[0-9]{3})\s*EUR", text):
            val = parse_eur(m.group(1))
            # Heuristic: LPG prices tend to be < 1.00
            if val < 1.0:
                prices["lpg"] = val if "lpg" not in prices or val < prices["lpg"] else prices["lpg"]
            else:
                # Could be A95 or Diesel; keep minimal seen
                if "a95" not in prices or val < prices["a95"]:
                    prices["a95"] = val
                if "diesel" not in prices or val < prices["diesel"]:
                    prices["diesel"] = val
        # Collect addresses
        addrs = []
        for m in re.finditer(r"(Rīga|Jelgava|Liepāja|Daugavpils|Valdeķu|Saharova|Biķernieku|Astras|Dārzciema)[^,]*[^\s]", text):
            addrs.append(m.group(0))
        stations = []
        for addr in addrs[:20]:
            stations.append({
                "name": "Viada",
                "address": addr,
                "prices": prices.copy(),
                "source": "viada",
                "timestamp": ""
            })
        return stations
