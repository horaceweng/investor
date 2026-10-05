"""選股儀表板: 資料來源 -> 選股/評級 -> 本機網頁。

分層 (依賴方向由下往上, 不可反向):
  paths, fileio, universe        基礎: 路徑集中、原子寫入、成分股
  data_sources/                  外部資料存取: Yahoo / SEC EDGAR / 股價表現
  screens/, superinvestors/, navellier/   各種選股與評級 (只依賴上面兩層, 彼此獨立)
  web/                           伺服器、背景工作、狀態、頁面渲染 (把以上組合起來)
"""
