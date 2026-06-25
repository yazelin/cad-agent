import uvicorn

def main() -> None:
    uvicorn.run("cad_agent.server:app", host="127.0.0.1", port=8099, reload=False)

if __name__ == "__main__":
    main()
