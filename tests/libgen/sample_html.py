"""Inline HTML samples for Libgen tests, trimmed to the real libgen.li structure.

The results table mixes two row shapes with NO rowspans: full 9-cell rows
``[Title, Author, Publisher, Year, Language, Pages, Size, Ext, Mirrors]`` and compact
5-cell rows ``[Title, Pages, Size, Ext, Mirrors]``. The md5 lives in the Mirrors cell.
"""

MD5_A = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"  # 9-cell epub row (author + language present)
# A decoy md5 planted in row A's Title cell (as a cover-image URL). The parser must NOT pick
# it: md5 extraction is scoped to the Mirrors cell, so row A must resolve to MD5_A, not this.
DECOY_MD5 = "0000000000000000000000000000dead"
MD5_B = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"  # 9-cell off-topic name-drop (cbr) - must survive
MD5_C = "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3"  # 5-cell compact manga volume (cbr)
MD5_D = "d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"  # 5-cell compact manga volume (cbz)
GET_KEY = "TESTKEY0001"

# A #tablelibgen with: header, 2 full rows (1 on-topic, 1 off-topic name-drop), 2 compact
# manga rows, a 1-cell spacer (len < 5 -> skipped), and a 5-cell row with no md5 (-> skipped).
SEARCH_HTML = f"""
<html><body>
<table id="tablelibgen">
  <tr><th>Title</th><th>Author(s)</th><th>Publisher</th><th>Year</th><th>Language</th>
      <th>Pages</th><th>Size</th><th>Ext.</th><th>Mirrors</th></tr>
  <tr>
    <td><img src="/covers/thumb.jpg?md5={DECOY_MD5}"><a href="edition.php?id=1">One Piece, Vol. 1</a></td>
    <td>Eiichiro Oda</td><td>Viz Media</td><td>2003</td><td>English</td>
    <td>216</td><td>180&nbsp;MB</td><td>epub</td>
    <td><a href="/get.php?md5={MD5_A}">Libgen</a>
        <a href="https://annas-archive.org/md5/{MD5_A}">Anna's</a></td>
  </tr>
  <tr>
    <td>Ninja High School #127 Naruto, One Piece &amp; Kenshin</td>
    <td>Ben Dunn</td><td>Antarctic Press</td><td>2005</td><td>English</td>
    <td>24</td><td>8&nbsp;MB</td><td>cbr</td>
    <td><a href="/get.php?md5={MD5_B}">Libgen</a></td>
  </tr>
  <tr>
    <td>One Piece 515</td><td>19</td><td>6&nbsp;MB</td><td>cbr</td>
    <td><a href="/get.php?md5={MD5_C}">Libgen</a></td>
  </tr>
  <tr>
    <td>One Piece 516</td><td>20</td><td>7&nbsp;MB</td><td>cbz</td>
    <td><a href="/get.php?md5={MD5_D}">Libgen</a></td>
  </tr>
  <tr><td colspan="9">-- section separator --</td></tr>
  <tr>
    <td>Advertisement</td><td></td><td></td><td></td>
    <td><a href="/promo">Sponsored</a></td>
  </tr>
</table>
</body></html>
"""

# A challenge/error page: no results table -> _parse_results returns None (try next mirror).
NO_TABLE_HTML = """
<html><body><div id="challenge">Checking your browser...</div></body></html>
"""

# A well-formed but empty results table -> _parse_results returns [] (accepted as final).
EMPTY_TABLE_HTML = """
<html><body>
<table id="tablelibgen">
  <tr><th>Title</th><th>Ext.</th><th>Mirrors</th></tr>
</table>
</body></html>
"""

# An ads.php page: keyed GET link + the labelled metadata block (as visible text).
ADS_HTML = f"""
<html><head><title>Library Genesis</title></head><body>
<table>
  <tr><td><a href="/get.php?md5={MD5_A}&key={GET_KEY}"><h2>GET</h2></a></td></tr>
  <tr><td>Title: One Piece, Vol. 1 Series: One Piece Author(s): Eiichiro Oda
      Publisher: Viz Media Year: 2003 ISBN: 9781234567890 Language: English Pages: 216</td></tr>
</table>
</body></html>
"""

# An ads.php page with no GET link (resolution should fail).
ADS_HTML_NO_GET = """
<html><body><p>File not found.</p></body></html>
"""
