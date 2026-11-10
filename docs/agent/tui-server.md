# tui-server
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=tui-server
page=docs/agent/tui-server.md
modules=7
module=tui/src/server-config.ts symbols=VERSION,ROOT,WORKER,arg,loadEnvFile,workspace,stateDir,tasksDir,eventsDir,promptsDir,logsDir,serverFile,model,effort,profile,concurrency,maxSteps,maxOutput,maxWall,cellTimeout,allowRun,python,initialChecks,initialKey,serverLog,SOURCE_RECHECK_MS
entry=arg(name, fallback), loadEnvFile(); VERSION, ROOT, WORKER, workspace, stateDir, tasksDir, | eventsDir, promptsDir, logsDir, serverFile, model, effort, profile, concurrency, maxSteps, | maxOutput, maxWall, cellTimeout, allowRun, python, initialChecks, initialKey, serverLog, | SOURCE_RECHECK_MS
module=tui/src/server-http.ts symbols=json,route
entry=json(data, status=200) -> Response   one JSON answer with the server's headers | route(req, srv) -> Response | Promise<Response> | undefined   the route table; undefined only | after an upgrade
module=tui/src/server-journal.ts symbols=taskFile,eventFile,promptFile,logFile,appendLog,saveTask,appendEvent,readTaskEvents,loadTasks
entry=taskFile, eventFile, promptFile, logFile (id); saveTask(task); appendLog(task, record); | appendEvent(task, partial) -> TaskEvent; readTaskEvents(taskId, limit=600); loadTasks()
module=tui/src/server-state.ts symbols=boot,setInfo,setServer,stopServer,beginShutdown,tasks,taskById,running,nextSeq,sourceStatus,refreshChecks,broadcast,snapshot
entry=tasks, taskById, running, info, setInfo, setServer, stopServer, beginShutdown, nextSeq, | sourceStatus, refreshChecks, broadcast, snapshot
module=tui/src/server-tasks.ts symbols=createTask,pump,startTask,cancelTask
entry=createTask(prompt) -> Task   queue a new task, journal it, broadcast it, pump | pump()                       start queued tasks while a slot is free | startTask(task)              spawn the worker, read its streams, settle its status on exit | cancelTask(task)             queued -> cancelled, running -> SIGTERM then SIGKILL after 5 s
module=tui/src/server-worker.ts symbols=readWorkerStream,handleWorkerLine
entry=readWorkerStream(stream, onLine) -> Promise   split a byte stream into non-empty lines | handleWorkerLine(task, raw)                   journal + apply one worker line
module=tui/src/server.ts symbols=-
entry=Bun src/server.ts --workspace DIR [--state DIR] [--log FILE] | server.json (ServerInfo) + one launch line {event,pid,port,profile,check,log}; port 0 = | OS-chosen. Routes: server-http.ts route(). SIGINT/SIGTERM -> shutdown().
