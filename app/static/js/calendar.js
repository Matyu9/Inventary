(function () {
  const el = document.getElementById("calendar");
  if (!el || !window.FullCalendar) return;
  const mobile = window.matchMedia("(max-width: 768px)").matches;
  const calendar = new FullCalendar.Calendar(el, {
    locale: "fr",
    initialView: mobile ? "listMonth" : "dayGridMonth",
    headerToolbar: mobile
      ? { left: "prev,next", center: "title", right: "listMonth,dayGridMonth" }
      : { left: "prev,next today", center: "title", right: "dayGridMonth,timeGridWeek,listMonth" },
    height: "auto",
    nowIndicator: true,
    events: el.dataset.eventsUrl,
    eventDidMount: function (info) {
      const status = info.event.extendedProps.status;
      if (status) info.el.title = info.event.title + " (" + status + ")";
    },
  });
  calendar.render();
})();
