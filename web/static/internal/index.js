const search = (searchTerm) => {
  const channels = document.querySelectorAll('a.card[data-channel-id]');

  // Update URL search parameter
  updateUrlParameter('search', searchTerm);

  channels.forEach((channel) => {
    const nameElement = channel.querySelector('.font-bold');
    const name = (channel.dataset.channelName || nameElement?.textContent || '').toLowerCase();
    channel.style.display = name.includes(searchTerm.toLowerCase()) ? '' : 'none';
  });
};

const showLoginWarning = () => {
  const toast = document.getElementById("login-warning-toast");
  if (!toast) return;
  toast.classList.add("is-visible");
  window.clearTimeout(showLoginWarning.timer);
  showLoginWarning.timer = window.setTimeout(() => {
    toast.classList.remove("is-visible");
  }, 3200);
};

const warnLoginRequired = (event) => {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  showLoginWarning();
  if (typeof login_modal !== "undefined" && login_modal.showModal) {
    login_modal.showModal();
  }
  return false;
};

const init = () => {
  const searchInput = safeGetElementById('portexe-search-input');

  // Check for search parameter on page load
  const urlParams = getCurrentUrlParams();
  const searchParam = urlParams.get('search');

  if (searchParam && searchInput) {
    search(searchParam);
    searchInput.value = searchParam;
  }

  if (searchInput) {
    searchInput.addEventListener('keyup', (e) => {
      search(e.target.value);
    });
  }

  if (urlParams.get("login") === "required") {
    showLoginWarning();
  }
};

// Call the init function to start the process
init();



const loginOTPClick = () => {
  const numberElement = safeGetElementById("number");
  if (!numberElement) {
    return;
  }

  const number = numberElement.value;
  if (!number) {
    return;
  }

  postJSON("/login/sendOTP", { number: `+91${number}` })
    .then((data) => {
      if (data.status) {
        verify_otp_modal.showModal(); // skipcq: JS-0125
      } else {
        alert("Sending OTP failed!");
      }
    })
    .catch((err) => {
      console.log(err);
      alert("Sending OTP failed!");
    });
};

const loginOTPVerifyClick = () => {
  const elements = safeGetElementsById(["number", "otp"]);
  const { number: numberElement, otp: otpElement } = elements;

  if (!numberElement || !otpElement) {
    return;
  }

  const number = numberElement.value;
  const otp = otpElement.value;

  if (!number || !otp) {
    return;
  }

  postJSON("/login/verifyOTP", { number: `+91${number}`, otp })
    .then((data) => {
      if (data.status) {
        alert("OTP verification success. Enjoy!");
        document.location.reload();
      } else {
        alert("OTP verification failed!");
      }
    })
    .catch((err) => {
      console.log(err);
      alert("OTP verification failed!");
    });
};
