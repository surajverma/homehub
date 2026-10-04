            // Highlight active sidebar link
            (() => {
                const links = document.querySelectorAll('#sidebarNav .sidebar-link');
                const path = window.location.pathname.replace(/\/$/, '');
                links.forEach(link => {
                    let href = link.getAttribute('href').replace(/\/$/, '');
                    // Only mark active if exact match
                    if (href === path) {
                        link.classList.add('active');
                    } else {
                        link.classList.remove('active');
                    }
                });
            })();
            
