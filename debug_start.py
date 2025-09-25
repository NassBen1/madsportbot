import os, sys, glob, pkgutil, traceback
print("PY:", sys.version)
print("CWD:", os.getcwd())
print("FILES:", [p for p in glob.glob("**/*.py", recursive=True)])
print("fastapi?", bool(pkgutil.find_loader("fastapi")))
print("uvicorn?", bool(pkgutil.find_loader("uvicorn")))
print("aiogram?", bool(pkgutil.find_loader("aiogram")))
print("pydantic?", bool(pkgutil.find_loader("pydantic")))
try:
    import webhook_app
    print("✅ import webhook_app OK")
except Exception:
    print("❌ import webhook_app KO:")
    traceback.print_exc()
    raise
