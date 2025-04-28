# AWS EC2 Scientific Computing Server

This project documents the architecture and purpose of a cloud-hosted EC2 instance configured for graduate-level scientific research in geochemistry and petrology. It is built to support reproducible thermodynamic modeling, trace element analysis, and Python/Julia-based computation workflows via remote access and containerized environments. The infrastructure is centered on practical utility, modularity, and long-term scalability.

## Overview

The server is hosted on an Amazon Web Services (AWS) Elastic Compute Cloud (EC2) instance and serves as a persistent, cloud-based environment for executing research-related code, particularly high-load or dependency-sensitive programs such as MELTS-based modeling tools. The goal is to ensure a consistent and reproducible computing setup regardless of the local machine, facilitating collaborative work, computational reproducibility, and long-term storage of results and scripts.

## Use Cases

- Thermodynamic simulations (e.g., Rhyolite-MELTS, ThermoEngine)
- Geochemical modeling of magma evolution
- Trace element spider diagram generation
- Statistical analysis and data visualization using Python and Jupyter
- Scheduled GitHub backups and automated synchronization
- Remote development of Python and Julia packages for modular research workflows

## System Specifications

- **Cloud Provider**: Amazon Web Services (AWS)
- **Instance Type**: t3.micro (scalable as needed)
- **Operating System**: Amazon Linux 2
- **Remote Interface**: JupyterLab (served over HTTPS)
- **Domain**: `https://lab.marvinlopezacevedo.com`
- **SSL Security**: Enabled via domain-level TLS certificates
- **Access Control**: SSH keys only; public password access is disabled

## Structure

All work is contained within a master repository directory (`db_sci_cluster/`) organized for clean modularity. Each subdirectory is focused on a specific function of the server or the research workflow.



## Notable Features

### JupyterLab Access

JupyterLab is served securely at `https://lab.marvinlopezacevedo.com` and configured to support multiple computational kernels:

- **Python 3.10+** (for general scientific computing)
- **Julia 1.9+** (for numerical modeling, AI tools, and potential ML extensions)
- **Container-based Interpreters** for encapsulated tools such as ThermoEngine

### Git Integration and Backups

The server supports scheduled GitHub uploads to maintain synchronized research scripts and notebooks. A backup script is scheduled via `cron` to commit and push files at:
- 12:00 PM Central Time
- 12:00 AM Central Time

These backups are useful for code versioning, collaboration, and long-term reproducibility.

### Dockerized Modeling Environments

Scientific tools such as ThermoEngine are installed inside Docker containers. This avoids dependency conflicts and ensures that computational environments remain portable and reproducible. Containers can be spun up manually or integrated into JupyterLab as back-end kernels.

### Security

- All traffic to JupyterLab is encrypted via HTTPS.
- SSH access is restricted to machines holding authorized private keys.
- `.ssh/`, API keys, and sensitive configuration files are excluded via `.gitignore`.

### Monitoring and Utility Scripts

The `server_tools/` folder contains scripts for:
- Logging and displaying storage and CPU usage in real-time
- Initializing environments on SSH login
- Running diagnostic or performance tests
- Future logging of modeling runs (e.g., MELTS batches)

### Manual Activation (Current Limitation)

At present, Conda environments must be manually activated upon login. 

## Future Directions

- **GPU acceleration** for high-temperature fluid and melt transport simulations  
- **Automated MELTS batch runners** with CSV input for parametric model sweeps  
- **Machine Learning integration** for classifying trace element signatures or model predictions  
- **Cloud storage connection** to AWS S3 or Glacier for long-term dataset archiving  
- **Authentication portal** for controlled multi-user collaboration  

## Author

**Marvin Lopez Acevedo**  
Graduate Researcher – Magmatic Systems, Geochemistry  
Fisk–Vanderbilt Master’s to PhD Bridge Program  
Department of Earth and Environmental Sciences  
Vanderbilt University  
 
- Website: [https://marvinlopezacevedo.com](https://marvinlopezacevedo.com)  



