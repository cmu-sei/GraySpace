# GraySpace - Internet Service Providers

TODO: Document ISP architecture

## Lumen

- **Tier:** Tier 1
- **ASN:** 3356
- **Public Block:** 6.0.0.0/16

### Routers

| Hostname | Location      | Roles  | Loopback IP | Customer Network | OSPF Areas |
| :------- | :------------ | :----- | :---------- | :--------------- | :--------- |
| lum-sfr  | San Francisco | ABR    | 6.0.1.1/32  |                  | 0,10       |
| lum-por  | Portland      | ABR    | 6.0.1.2/32  |                  | 0,10       |
| lum-kck  | Kansas City   | ABR    | 6.0.1.3/32  |                  | 0,20       |
| lum-mex  | Mexico City   |        | 6.0.1.4/32  | 200.38.194.0/24  | 20         |
| lum-chi  | Chicago       |        | 6.0.1.5/32  | 209.151.130.0/24 | 10         |
| lum-nyc  | New York      |        | 6.0.1.6/32  | 63.211.218.0/24  | 10         |
| lum-ash  | Ashburn       | ABR,RR | 6.0.1.7/32  |                  | 0,10       |
| lum-for  | Fortaleza     | ABR    | 6.0.1.8/32  |                  | 0,30       |
| lum-lim  | Lima          |        | 6.0.1.9/32  | 200.48.243.0/24  | 30         |
| lum-brs  | Brasilia      |        | 6.0.1.10/32 | 200.223.128.0/24 | 30         |
| lum-lon  | London        | ABR    | 6.0.1.11/32 |                  | 0,40       |
| lum-fra  | Frankfurt     |        | 6.0.1.12/32 | 213.242.111.0/24 | 40         |
| lum-hel  | Helsinki      |        | 6.0.1.13/32 | 194.156.121.0/24 | 40         |
| lum-mos  | Moscow        |        | 6.0.1.14/32 | 212.162.33.0/24  | 40         |
| lum-cai  | Cairo         | ABR,RR | 6.0.1.15/32 |                  | 0,40,50    |
| lum-dub  | Dubai         |        | 6.0.1.16/32 | 212.77.192.0/24  | 50         |
| lum-dji  | Djibouti      | ABR    | 6.0.1.17/32 |                  | 0,60       |
| lum-lag  | Lagos         | ABR    | 6.0.1.18/32 |                  | 0,60,70    |
| lum-alg  | Algiers       |        | 6.0.1.19/32 | 41.222.192.0/24  | 70         |
| lum-joh  | Johannesburg  |        | 6.0.1.20/32 | 41.150.128.0/24  | 60         |
| lum-mum  | Mumbai        | ABR    | 6.0.1.21/32 |                  | 0,50       |
| lum-hkg  | Hong Kong     | ABR    | 6.0.1.22/32 |                  | 0,80       |
| lum-man  | Manila        |        | 6.0.1.23/32 | 202.137.96.0/24  | 80         |
| lum-sin  | Singapore     |        | 6.0.1.24/32 | 203.127.192.0/24 | 80         |
| lum-tok  | Tokyo         | ABR    | 6.0.1.25/32 |                  | 0,90       |
| lum-seo  | Seoul         |        | 6.0.1.26/32 | 203.254.80.0/24  | 90         |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type     |
| :------- | :---------- | :-------------------- |
| Comcast  | com-sfr     | Customer (Downstream) |
| Rogers   | rog-tor     | Customer (Downstream) |
| Claro    | cla-qui     | Customer (Downstream) |
| Liquid   | liq-abj     | Customer (Downstream) |
| STC      | stc-dam     | Customer (Downstream) |
| Telmex   | tel-gua     | Customer (Downstream) |
| Arelion  | are-ash     | Peering Partner       |
| NTT      | ntt-ash     | Peering Partner       |
| Arelion  | are-lon     | Peering Partner       |
| Arelion  | are-cai     | Peering Partner       |
| NTT      | ntt-cai     | Peering Partner       |
| Arelion  | are-hkg     | Peering Partner       |
| NTT      | ntt-hkg     | Peering Partner       |

---

## Arelion

- **Tier:** Tier 1
- **ASN:** 1299
- **Public Block:** 7.0.0.0/16

### Routers

| Hostname | Location      | Roles  | Loopback IP | Customer Network | OSPF Areas |
| :------- | :------------ | :----- | :---------- | :--------------- | :--------- |
| are-sfr  | San Francisco |        | 7.0.1.1/32  | 204.148.19.0/24  | 10         |
| are-lax  | Los Angeles   | ABR    | 7.0.1.2/32  |                  | 0,10       |
| are-den  | Denver        | ABR    | 7.0.1.3/32  |                  | 0,10       |
| are-chi  | Chicago       |        | 7.0.1.4/32  | 64.215.144.0/24  | 10         |
| are-ash  | Ashburn       | ABR,RR | 7.0.1.5/32  |                  | 0,10       |
| are-for  | Fortaleza     | ABR    | 7.0.1.6/32  |                  | 0,20       |
| are-bog  | Bogota        |        | 7.0.1.7/32  | 200.13.238.0/24  | 20         |
| are-san  | Santiago      | ABR    | 7.0.1.8/32  | 200.29.16.0/24   | 0,20       |
| are-lon  | London        | ABR    | 7.0.1.9/32  |                  | 0,30       |
| are-par  | Paris         |        | 7.0.1.10/32 | 89.46.228.0/24   | 30         |
| are-lis  | Lisbon        |        | 7.0.1.11/32 | 91.196.216.0/24  | 30         |
| are-fra  | Frankfurt     | ABR    | 7.0.1.12/32 |                  | 0,30,40    |
| are-hel  | Helsinki      |        | 7.0.1.13/32 | 80.249.209.0/24  | 40         |
| are-mos  | Moscow        |        | 7.0.1.14/32 | 94.142.136.0/24  | 40         |
| are-cai  | Cairo         | ABR    | 7.0.1.15/32 |                  | 0,50       |
| are-lag  | Lagos         | ABR    | 7.0.1.16/32 |                  | 0,50       |
| are-dji  | Djibouti      |        | 7.0.1.17/32 | 102.132.227.0/24 | 50         |
| are-dub  | Dubai         | ABR    | 7.0.1.18/32 |                  | 0,50       |
| are-del  | Delhi         | ABR    | 7.0.1.19/32 |                  | 0,60       |
| are-mum  | Mumbai        |        | 7.0.1.20/32 | 116.206.32.0/24  | 60         |
| are-che  | Chennai       |        | 7.0.1.21/32 | 111.93.125.0/24  | 60         |
| are-hkg  | Hong Kong     | ABR,RR | 7.0.1.22/32 |                  | 0,70,80    |
| are-seo  | Seoul         |        | 7.0.1.23/32 | 118.221.112.0/24 | 70         |
| are-man  | Manila        |        | 7.0.1.24/32 | 122.53.116.0/24  | 80         |
| are-sin  | Singapore     | ABR    | 7.0.1.25/32 |                  | 0,80       |
| are-per  | Perth         | ABR    | 7.0.1.26/32 |                  | 0,90       |
| are-syd  | Sydney        | ABR    | 7.0.1.27/32 |                  | 0,90       |
| are-ade  | Adelaide      |        | 7.0.1.28/32 | 125.7.128.0/24   | 90         |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type     |
| :------- | :---------- | :-------------------- |
| Lumen    | lum-ash     | Peering Partner       |
| NTT      | ntt-ash     | Peering Partner       |
| Lumen    | lum-lon     | Peering Partner       |
| Lumen    | lum-cai     | Peering Partner       |
| NTT      | ntt-cai     | Peering Partner       |
| Lumen    | lum-hkg     | Peering Partner       |
| NTT      | ntt-hkg     | Peering Partner       |
| Claro    | cla-car     | Customer (Downstream) |
| Seaborn  | sea-sal     | Customer (Downstream) |
| TDC      | tdc-pra     | Customer (Downstream) |
| STC      | stc-dub     | Customer (Downstream) |
| Singtel  | sin-sin     | Customer (Downstream) |
| Telmex   | tel-oax     | Customer (Downstream) |
| BSNL     | bns-kol     | Customer (Downstream) |

---

## NTT

- **Tier:** Tier 1
- **ASN:** 2914
- **Public Block:** 26.0.0.0/16

### Routers

| Hostname | Location     | Roles  | Loopback IP  | Customer Network | OSPF Areas |
| :------- | :----------- | :----- | :----------- | :--------------- | :--------- |
| ntt-por  | Portland     | ABR    | 26.0.1.1/32  |                  | 0,10       |
| ntt-lax  | Los Angeles  | ABR    | 26.0.1.2/32  |                  | 0,10       |
| ntt-chi  | Chicago      |        | 26.0.1.3/32  | 129.250.3.0/24   | 10         |
| ntt-ash  | Ashburn      | ABR,RR | 26.0.1.4/32  |                  | 0,10       |
| ntt-lis  | Lisbon       |        | 26.0.1.5/32  | 212.119.33.0/24  | 0          |
| ntt-san  | Santiago     | ABR    | 26.0.1.6/32  |                  | 0,20       |
| ntt-lim  | Lima         |        | 26.0.1.7/32  | 200.60.101.0/24  | 20         |
| ntt-brs  | Brasilia     |        | 26.0.1.8/32  | 177.85.40.0/24   | 20         |
| ntt-bai  | Buenos Aires | ABR    | 26.0.1.9/32  |                  | 0,20       |
| ntt-cai  | Cairo        | ABR    | 26.0.1.10/32 |                  | 0,30       |
| ntt-kyv  | Kyiv         |        | 26.0.1.11/32 | 93.178.204.0/24  | 30         |
| ntt-mos  | Moscow       |        | 26.0.1.12/32 | 46.16.88.0/24    | 30         |
| ntt-dji  | Djibouti     | ABR    | 26.0.1.13/32 |                  | 0,40       |
| ntt-lag  | Lagos        |        | 26.0.1.14/32 | 41.217.160.0/24  | 40         |
| ntt-joh  | Johannesburg | ABR    | 26.0.1.15/32 |                  | 0,40       |
| ntt-dub  | Dubai        |        | 26.0.1.16/32 | 163.121.128.0/24 | 0          |
| ntt-del  | Delhi        | ABR,RR | 26.0.1.17/32 |                  | 0,30,50    |
| ntt-che  | Chennai      |        | 26.0.1.18/32 | 111.68.96.0/24   | 50         |
| ntt-krs  | Krasnoyarsk  |        | 26.0.1.19/32 | 217.107.192.0/24 | 30         |
| ntt-hkg  | Hong Kong    | ABR    | 26.0.1.20/32 |                  | 0,30,60    |
| ntt-man  | Manila       | ABR    | 26.0.1.21/32 |                  | 0,60       |
| ntt-sin  | Singapore    |        | 26.0.1.22/32 | 203.116.0.0/24   | 60         |
| ntt-dar  | Darwin       |        | 26.0.1.23/32 | 14.200.0.0/24    | 60         |
| ntt-per  | Perth        |        | 26.0.1.24/32 | 150.107.64.0/24  | 60         |
| ntt-syd  | Sydney       |        | 26.0.1.25/32 | 203.49.208.0/24  | 60         |
| ntt-seo  | Seoul        |        | 26.0.1.26/32 | 210.155.128.0/24 | 30         |
| ntt-tok  | Tokyo        | ABR    | 26.0.1.27/32 |                  | 0,30       |
| ntt-vla  | Vladivostok  |        | 26.0.1.28/32 | 212.109.192.0/24 | 30         |

### BGP Peers

| Peer ISP      | Peer Router | Relationship Type     |
| :------------ | :---------- | :-------------------- |
| Lumen         | lum-ash     | Peering Partner       |
| Lumen         | lum-cai     | Peering Partner       |
| Lumen         | lum-hkg     | Peering Partner       |
| Comcast       | com-ash     | Customer (Downstream) |
| Rogers        | rog-van     | Customer (Downstream) |
| Seaborn       | sea-sao     | Customer (Downstream) |
| Liquid        | liq-cpt     | Customer (Downstream) |
| TDC           | tdc-mad     | Customer (Downstream) |
| MTS           | mts-mos     | Customer (Downstream) |
| MTS           | mts-vla     | Customer (Downstream) |
| China Telecom | ctl-sha     | Customer (Downstream) |
| Singtel       | sin-brs     | Customer (Downstream) |
| BSNL          | bns-del     | Customer (Downstream) |
| Arelion       | are-ash     | Peering Partner       |
| Arelion       | are-cai     | Peering Partner       |
| Arelion       | are-hkg     | Peering Partner       |

---

## Comcast

- **Tier:** Tier 2
- **ASN:** 7922
- **Public Block:** 21.0.0.0/16

### Routers

| Hostname | Location      | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :------------ | :---- | :---------- | :--------------- | :--------- |
| com-por  | Portland      |       | 21.0.1.1/32 | 98.245.128.0/24  | 0          |
| com-sfr  | San Francisco |       | 21.0.1.2/32 | 73.158.0.0/24    | 0          |
| com-den  | Denver        |       | 21.0.1.3/32 | 75.76.0.0/24     | 0          |
| com-hou  | Houston       | RR    | 21.0.1.4/32 | 98.192.0.0/24    | 0          |
| com-chi  | Chicago       | RR    | 21.0.1.5/32 | 50.232.0.0/24    | 0          |
| com-ash  | Ashburn       |       | 21.0.1.6/32 | 68.85.0.0/24     | 0          |
| com-nyc  | New York      |       | 21.0.1.7/32 | 69.240.0.0/24    | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Rogers   | rog-tor     | Peering Partner     |
| NTT      | ntt-ash     | Provider (Upstream) |
| Lumen    | lum-sfr     | Provider (Upstream) |

---

## Rogers

- **Tier:** Tier 2
- **ASN:** 812
- **Public Block:** 48.0.0.0/16

### Routers

| Hostname | Location  | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :-------- | :---- | :---------- | :--------------- | :--------- |
| rog-van  | Vancouver |       | 48.0.1.1/32 | 24.114.0.0/24    | 0          |
| rog-edm  | Edmonton  |       | 48.0.1.2/32 | 74.198.0.0/24    | 0          |
| rog-wpg  | Winnipeg  | RR    | 48.0.1.3/32 | 24.109.0.0/24    | 0          |
| rog-tor  | Toronto   |       | 48.0.1.4/32 | 64.231.0.0/24    | 0          |
| rog-mtl  | Montreal  |       | 48.0.1.5/32 | 206.47.0.0/24    | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Comcast  | com-nyc     | Peering Partner     |
| NTT      | ntt-por     | Provider (Upstream) |
| Lumen    | lum-nyc     | Provider (Upstream) |

---

## China Telecom

- **Tier:** Tier 2
- **ASN:** 4812
- **Public Block:** 36.0.0.0/16

### Routers

| Hostname | Location  | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :-------- | :---- | :---------- | :--------------- | :--------- |
| ctl-bei  | Beijing   |       | 36.0.1.1/32 | 202.96.0.0/24    | 0          |
| ctl-sha  | Shanghai  | RR    | 36.0.1.2/32 | 116.228.0.0/24   | 0          |
| ctl-hkg  | Hong Kong |       | 36.0.1.3/32 | 218.102.0.0/24   | 0          |
| ctl-chg  | Chengdu   |       | 36.0.1.4/32 | 118.122.0.0/24   | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Singtel  | sin-hcm     | Peering Partner     |
| NTT      | ntt-hkg     | Provider (Upstream) |

---

## Singtel

- **Tier:** Tier 2
- **ASN:** 7473
- **Public Block:** 49.0.0.0/16

### Routers

| Hostname | Location         | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :--------------- | :---- | :---------- | :--------------- | :--------- |
| sin-tok  | Tokyo            |       | 49.0.1.1/32 | 165.21.80.0/24   | 0          |
| sin-hcm  | Ho Chi Minh City |       | 49.0.1.2/32 | 123.30.0.0/24    | 0          |
| sin-sin  | Singapore        |       | 49.0.1.3/32 | 165.21.16.0/24   | 0          |
| sin-dil  | Dili             | RR    | 49.0.1.4/32 | 113.20.96.0/24   | 0          |
| sin-per  | Perth            | RR    | 49.0.1.5/32 | 101.160.0.0/24   | 0          |
| sin-brs  | Brisbane         |       | 49.0.1.6/32 | 110.174.128.0/24 | 0          |
| sin-mel  | Melbourne        |       | 49.0.1.7/32 | 124.190.0.0/24   | 0          |

### BGP Peers

| Peer ISP      | Peer Router | Relationship Type   |
| :------------ | :---------- | :------------------ |
| China Telecom | ctl-hkg     | Peering Partner     |
| NTT           | ntt-syd     | Provider (Upstream) |
| Arelion       | are-sin     | Provider (Upstream) |

---

## Liquid

- **Tier:** Tier 2
- **ASN:** 37100
- **Public Block:** 57.0.0.0/16

### Routers

| Hostname | Location  | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :-------- | :---- | :---------- | :--------------- | :--------- |
| liq-alg  | Algiers   |       | 57.0.1.1/32 | 197.231.128.0/24 | 0          |
| liq-abj  | Abidjan   | RR    | 57.0.1.2/32 | 102.68.128.0/24  | 0          |
| liq-mom  | Mombasa   | RR    | 57.0.1.3/32 | 197.248.0.0/24   | 0          |
| liq-cpt  | Cape Town |       | 57.0.1.4/32 | 197.220.192.0/24 | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| TDC      | tdc-mad     | Peering Partner     |
| NTT      | ntt-joh     | Provider (Upstream) |
| Lumen    | lum-lag     | Provider (Upstream) |

---

## Seaborn

- **Tier:** Tier 2
- **ASN:** 52789
- **Public Block:** 91.0.0.0/16

### Routers

| Hostname | Location  | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :-------- | :---- | :---------- | :--------------- | :--------- |
| sea-brs  | Brasilia  |       | 91.0.1.1/32 | 138.36.192.0/24  | 0          |
| sea-sal  | Salvador  |       | 91.0.1.2/32 | 138.185.128.0/24 | 0          |
| sea-sao  | Sao Paulo |       | 91.0.1.3/32 | 138.204.128.0/24 | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Claro    | cla-lap     | Peering Partner     |
| Arelion  | are-for     | Provider (Upstream) |
| NTT      | ntt-brs     | Provider (Upstream) |

---

## MTS

- **Tier:** Tier 2
- **ASN:** 8359
- **Public Block:** 217.0.0.0/16

### Routers

| Hostname | Location    | Roles | Loopback IP  | Customer Network | OSPF Areas |
| :------- | :---------- | :---- | :----------- | :--------------- | :--------- |
| mts-mos  | Moscow      |       | 217.0.1.1/32 | 212.188.0.0/24   | 0          |
| mts-agb  | Ashgabat    |       | 217.0.1.2/32 | 62.205.128.0/24  | 0          |
| mts-krs  | Krasnoyarsk |       | 217.0.1.3/32 | 85.235.128.0/24  | 0          |
| mts-vla  | Vladivostok |       | 217.0.1.4/32 | 195.161.64.0/24  | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| STC      | stc-teh     | Peering Partner     |
| TDC      | tdc-war     | Peering Partner     |
| NTT      | ntt-mos     | Provider (Upstream) |
| NTT      | ntt-vla     | Provider (Upstream) |

---

## Telmex

- **Tier:** Tier 2
- **ASN:** 8151
- **Public Block:** 200.0.0.0/16

### Routers

| Hostname | Location    | Roles | Loopback IP  | Customer Network | OSPF Areas |
| :------- | :---------- | :---- | :----------- | :--------------- | :--------- |
| tel-gua  | Guadalajara |       | 200.0.1.1/32 | 201.174.0.0/24   | 0          |
| tel-mex  | Mexico City |       | 200.0.1.2/32 | 201.134.128.0/24 | 0          |
| tel-oax  | Oaxaca      |       | 200.0.1.3/32 | 201.159.0.0/24   | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Lumen    | lum-mex     | Provider (Upstream) |
| Arelion  | are-bog     | Provider (Upstream) |

---

## Claro

- **Tier:** Tier 2
- **ASN:** 7303
- **Public Block:** 201.0.0.0/16

### Routers

| Hostname | Location | Roles | Loopback IP  | Customer Network | OSPF Areas |
| :------- | :------- | :---- | :----------- | :--------------- | :--------- |
| cla-qui  | Quito    |       | 201.0.1.1/32 | 190.96.128.0/24  | 0          |
| cla-car  | Caracas  |       | 201.0.1.2/32 | 190.200.0.0/24   | 0          |
| cla-lap  | La Paz   |       | 201.0.1.3/32 | 200.87.64.0/24   | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Seaborn  | sea-brs     | Peering Partner     |
| Lumen    | lum-lim     | Provider (Upstream) |
| Arelion  | are-bog     | Provider (Upstream) |

---

## BSNL

- **Tier:** Tier 2
- **ASN:** 9829
- **Public Block:** 117.0.0.0/16

### Routers

| Hostname | Location  | Roles | Loopback IP  | Customer Network | OSPF Areas |
| :------- | :-------- | :---- | :----------- | :--------------- | :--------- |
| bns-del  | Delhi     |       | 117.0.1.1/32 | 218.248.0.0/24   | 0          |
| bns-hyd  | Hyderabad |       | 117.0.1.2/32 | 117.200.128.0/24 | 0          |
| bns-kol  | Kolkata   |       | 117.0.1.3/32 | 59.160.0.0/24    | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Arelion  | are-che     | Provider (Upstream) |
| NTT      | ntt-del     | Provider (Upstream) |

---

## STC

- **Tier:** Tier 2
- **ASN:** 39386
- **Public Block:** 82.0.0.0/16

### Routers

| Hostname | Location | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :------- | :---- | :---------- | :--------------- | :--------- |
| stc-dam  | Damascus |       | 82.0.1.1/32 | 82.137.192.0/24  | 0          |
| stc-riy  | Riyadh   | RR    | 82.0.1.2/32 | 86.108.0.0/24    | 0          |
| stc-teh  | Tehran   | RR    | 82.0.1.3/32 | 91.98.0.0/24     | 0          |
| stc-dub  | Dubai    |       | 82.0.1.4/32 | 78.93.128.0/24   | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| MTS      | mts-agb     | Peering Partner     |
| Lumen    | lum-cai     | Provider (Upstream) |
| Arelion  | are-dub     | Provider (Upstream) |

---

## TDC

- **Tier:** Tier 2
- **ASN:** 3292
- **Public Block:** 84.0.0.0/16

### Routers

| Hostname | Location   | Roles | Loopback IP | Customer Network | OSPF Areas |
| :------- | :--------- | :---- | :---------- | :--------------- | :--------- |
| tdc-mad  | Madrid     |       | 84.0.1.1/32 | 80.160.0.0/24    | 0          |
| tdc-cop  | Copenhagen | RR    | 84.0.1.2/32 | 195.215.0.0/24   | 0          |
| tdc-pra  | Prague     | RR    | 84.0.1.3/32 | 212.96.160.0/24  | 0          |
| tdc-war  | Warsaw     |       | 84.0.1.4/32 | 83.0.0.0/24      | 0          |
| tdc-buc  | Bucharest  |       | 84.0.1.5/32 | 82.76.0.0/24     | 0          |

### BGP Peers

| Peer ISP | Peer Router | Relationship Type   |
| :------- | :---------- | :------------------ |
| Liquid   | liq-alg     | Peering Partner     |
| MTS      | mts-mos     | Peering Partner     |
| NTT      | ntt-lis     | Provider (Upstream) |
| Arelion  | are-fra     | Provider (Upstream) |
