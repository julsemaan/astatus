.PHONY: bump-version

bump-version:
	@b="$(or $(BUMP),patch)"; \
	{ echo 'import json'; \
	  echo 'p=json.load(open("package.json"))'; \
	  echo 'v=[int(x) for x in p["version"].split(".")]'; \
	  echo "b='$$b'"; \
	  echo 'if b=="major": v=[v[0]+1,0,0]'; \
	  echo 'elif b=="minor": v=[v[0],v[1]+1,0]'; \
	  echo 'else: v[2]+=1'; \
	  echo 'p["version"]=".".join(map(str,v))'; \
	  echo 'json.dump(p,open("package.json","w"),indent=2)'; \
	  echo 'open("package.json","a").write(chr(10))'; \
	  echo 'print("Bumped to "+p["version"])'; \
	} | python3 && npm install --package-lock-only --silent
