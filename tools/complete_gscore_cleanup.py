"""Finish only the known migration scratch cleanup; do not alter access controls."""
import datetime,json,pathlib,shutil
ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE=ROOT/'integrations'
REPORT=BASE/'handoff-cleanup.json'
def main():
 if REPORT.exists():
  print(json.dumps({'state':'existing_checkpoint','path':'integrations/handoff-cleanup.json'}));return
 activation=json.loads((BASE/'activation-status.json').read_text(encoding='utf-8'))
 backup=pathlib.Path(activation['backup']).resolve()
 assert backup.is_relative_to((ROOT/'backups').resolve()) and (backup/'retired-source-hashes.json').is_file()
 history=backup/'migration-history';history.mkdir(exist_ok=True)
 report={'checked_at':datetime.datetime.now().astimezone().isoformat(),'state':'running','archived':[],'removed':[],'already_absent':[],'retained':[],'permission_changes':False,'service_restarts':False}
 def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 save()
 for name in ('migration-stage','migration-candidate.b64','activation-precheck-1.json','preflight-runtime.json','stage-status.json','failed-fetches'):
  src=BASE/name;dst=history/name
  try:
   assert not src.is_symlink() and not dst.is_symlink()
   if src.exists():
    if dst.exists():raise FileExistsError()
    src.rename(dst);report['archived'].append(name)
   elif dst.exists():report['archived'].append(name)
   else:report['already_absent'].append(name)
  except (OSError,AssertionError) as e:report['retained'].append({'path':'integrations/'+name,'reason':type(e).__name__})
  save()
 for name in ('bundle-transfer','adapter-probe-work','gsuid_core-1153548720a7c04e8f29a0299e90131c81bc0521.zip','gsuid_core-direct-download.zip','nonebot-genshinuid-e57b76a089365357b3581bf605127842d1bd31dc.zip','current-verification'):
  p=BASE/name
  try:
   assert not p.is_symlink()
   if p.is_dir():shutil.rmtree(p);report['removed'].append(name)
   elif p.is_file():p.unlink();report['removed'].append(name)
   else:report['already_absent'].append(name)
  except (OSError,AssertionError) as e:report['retained'].append({'path':'integrations/'+name,'reason':type(e).__name__})
  save()
 report['state']='completed' if not report['retained'] else 'completed_with_residue'
 report['failed_download_contents']='preserved_in_backup_without_changing_permissions' if 'failed-fetches' in report['archived'] else 'see_retained'
 save();print(json.dumps(report,ensure_ascii=True))
if __name__=='__main__':main()
