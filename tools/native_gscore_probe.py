import os,pathlib,sys,json
ROOT=pathlib.Path(__file__).resolve().parents[1]
os.environ.update(QT_QPA_PLATFORM='offscreen',FURINA_ROOT=str(ROOT),PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
sys.path.insert(0,str(ROOT/'integrations/migration-stage/project'))
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app=QApplication([])
from panel.classic.games import GameManagementPage
from furina.services.gscore import snapshot
page=GameManagementPage();page.resize(1440,1000)
result={'loaded':False,'url':'','rendered':False}
def finish(ok=False):
 if page.web:
  result['url']=page.web.url().toString()
  result['rendered']=page.grab().save(str(ROOT/'logs/gscore-native-preflight.png'))
 result['loaded']=bool(ok)
 (ROOT/'integrations/native-preflight.json').write_text(json.dumps(result))
 app.quit()
page._result(snapshot(),'')
if page.web:page.web.loadFinished.connect(lambda ok:QTimer.singleShot(1500,lambda:finish(ok)))
page.show()
QTimer.singleShot(30000,lambda:finish(False))
app.exec()
