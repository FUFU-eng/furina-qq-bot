import pathlib,subprocess,json,sys,os
root=pathlib.Path(__file__).resolve().parents[1]
stage=root/'integrations/migration-stage/project'
python=root/'integrations/nonebot-env/Scripts/python.exe'
os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
status=root/'integrations/stage-tests.json'
status.write_text(json.dumps({'state':'running','pid':os.getpid()}))
with (root/'logs/gscore-stage-tests.log').open('wb') as log:
 compile_result=subprocess.run([str(python),'-m','compileall','-q','bot.py','furina','panel','tools','tests'],cwd=stage,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 result=subprocess.run([str(python),'-m','unittest','discover','-s','tests','-t','.'],cwd=stage,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW) if compile_result.returncode==0 else compile_result
status.write_text(json.dumps({'state':'completed','exit_code':result.returncode,'compiled':compile_result.returncode==0}))
