import os
import subprocess

server_process = None   # <<< thêm dòng này

def run_server():
    global server_process
    print("Starting RTSP Server on port 5544...")
    server_process = subprocess.Popen(["python", "Server.py", "5544"])
    print("Server started.\n")

def run_client():
    print("Starting RTP Client...")
    subprocess.Popen(["python", "ClientLauncher.py", "localhost", "5544", "5004", "movie.Mjpeg"])
    print("Client started.\n")

def stop_server():
    global server_process
    print("Stopping RTSP Server...")

    if server_process:
        server_process.terminate()
        server_process = None
        print("Server stopped.\n")
    else:
        print("No server is running.\n")

def main():
    while True:
        print("\n===== MENU =====")
        print("1. Run Server")
        print("2. Run Client")
        print("3. Exit")
        choice = input("Choose an option: ")

        if choice == "1":
            run_server()
        elif choice == "2":
            run_client()
        elif choice == "3":
            stop_server()
            print("Exiting...")
            break
        else:
            print("Invalid choice, try again!")

if __name__ == "__main__":
    main()
