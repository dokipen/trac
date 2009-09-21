
(function($){
  
  window.initializeFilters = function() {
    // Remove an existing row from the filters table
    function removeRow(button, propertyName) {
      var m = propertyName.match(/^(\d+)_(.*)$/);
      var clauseNum = m[1], field = m[2];
      var tr = $(button).closest("tr");
      
      // Keep the filter label when removing the first row
      var label = $("#label_" + propertyName);
      if (label.length && (label.closest("tr")[0] == tr[0])) {
        var next = tr.next("." + field);
        if (next.length) {
          var thisTh = tr.children().eq(0);
          var nextTh = next.children().eq(0);
          if (nextTh.attr("colSpan") == 1) {
            nextTh.replaceWith(thisTh);
          } else {
            nextTh.attr("colSpan", 1).before(thisTh);
            next.children().eq(1).replaceWith(tr.children().eq(0));
          }
        }
      }
      
      // Remove the row, filter tbody or clause tbody
      var tbody = tr.closest("tbody");
      if (tbody.children("tr").length > 1) {
        tr.remove();
      } else {
        var table = tbody.closest("table.trac-clause");
        var ctbody = table.closest("tbody");
        if (table.children().length > 3 || !ctbody.siblings().length) {
          tbody.remove();
        } else {
          var add_clause = $("#add_clause", ctbody);
          if (add_clause.length)
            $("tr.actions td.actions", ctbody.prev()).attr("colSpan", 2)
              .before(add_clause.closest("td"));
          if (ctbody.prev().length == 0)
            ctbody.next().children("tr:first").attr("style", "display: none");
          ctbody.remove();
          return;
        }
      }
      
      // Re-enable non-multiline filter
      $("#add_filter_" + clauseNum + " option[value='" + field + "']")
        .enable();
    }
    
    // Make the submit buttons for removing filters client-side triggers
    $("#filters input[type='submit'][name^='rm_filter_']").each(function() {
      var idx = this.name.search(/_\d+$/);
      if (idx < 0)
        idx = this.name.length;
      var propertyName = this.name.substring(10, idx);
      $(this).replaceWith($.create("input").attr("type", "button")
                           .val(this.value).click(function() {
        removeRow(this, propertyName);
        return false;
      }));
    });
    
    // Convenience function for creating a <label>
    function createLabel(text, htmlFor) {
      var label = $.create("label").text(text);
      if (htmlFor)
        label.attr("for", htmlFor).addClass("control");
      return label;
    }
    
    // Convenience function for creating an <input type="text">
    function createText(name, size) {
      return $.create("input").attr("type", "text").attr("name", name)
               .attr("size", size);
    }
    
    // Convenience function for creating an <input type="checkbox">
    function createCheckbox(name, value, id) {
      return $.create("input").attr("type", "checkbox").attr("id", id)
               .attr("name", name).val(value);
    }
    
    // Convenience function for creating an <input type="radio">
    function createRadio(name, value, id) {
      // Workaround for IE, otherwise the radio buttons are not selectable
      return $('<input type="radio" name="' + name + '"/>')
               .attr("id", id).val(value);
    }
    
    // Convenience function for creating a <select>
    function createSelect(name, options, optional) {
      var e = $.create("select").attr("name", name);
      if (optional)
        $.create("option").appendTo(e);
      for (var i = 0; i < options.length; i++) {
        if (typeof(options[i]) == "object")
          $.create("option").val(options[i].value).text(options[i].text)
            .appendTo(e);
        else
          $.create("option").val(options[i]).text(options[i]).appendTo(e);
      }
      return e;
    }
    
    // Make the drop-down menu for adding a filter a client-side trigger
    $("#filters select[name^=add_filter_]").change(function() {
      if (this.selectedIndex < 1)
        return;
      
      if (this.options[this.selectedIndex].disabled) {
        // IE doesn't support disabled options
        alert("A filter already exists for that property");
        this.selectedIndex = 0;
        return;
      }
      
      var propertyName = this.options[this.selectedIndex].value;
      var property = properties[propertyName];
      var table = $(this).closest("table.trac-clause")[0];
      var tbody = $("tr." + propertyName, table).closest("tbody").eq(0);
      var tr = $.create("tr").addClass(propertyName);
      
      var clauseNum = $(this).attr("name").split("_").pop();
      propertyName = clauseNum + "_" + propertyName;
      
      // Add the row header
      var th = $.create("th").attr("scope", "row");
      if (!tbody.length) {
        th.append(createLabel(property.label)
                    .attr("id", "label_" + propertyName));
      } else {
        th.attr("colSpan", property.type == "time"? 1: 2)
          .append(createLabel("or"))
      }
      tr.append(th);
      
      var td = $.create("td");
      var focusElement = null;
      if (property.type == "radio" || property.type == "checkbox"
          || property.type == "time") {
        td.addClass("filter").attr("colSpan", 2);
        if (property.type == "radio") {
          for (var i = 0; i < property.options.length; i++) {
            var option = property.options[i];
            td.append(createCheckbox(propertyName, option, 
                                     propertyName + "_" + option)).append(" ")
              .append(createLabel(option ? option : "none",
                                  propertyName + "_" + option)).append(" ");
          }
        } else if (property.type == "checkbox") {
          td.append(createRadio(propertyName, "1", propertyName + "_on"))
            .append(" ").append(createLabel("yes", propertyName + "_on"))
            .append(" ")
            .append(createRadio(propertyName, "0", propertyName + "_off"))
            .append(" ").append(createLabel("no", propertyName + "_off"));
        } else if (property.type == "time") {
          focusElement = createText(propertyName, 14)
          td.append(createLabel("between")).append(" ")
            .append(focusElement).append(" ")
            .append(createLabel("and")).append(" ")
            .append(createText(propertyName + "_end", 14));
        }
        tr.append(td);
      } else {
        if (!tbody.length) {
          // Add the mode selector
          td.addClass("mode")
            .append(createSelect(propertyName + "_mode", modes[property.type]))
            .appendTo(tr);
        }
        
        // Add the selector or text input for the actual filter value
        td = $.create("td").addClass("filter");
        if (property.type == "select") {
          focusElement = createSelect(propertyName, property.options, true);
        } else if ((property.type == "text") || (property.type == "id")
                   || (property.type == "textarea")) {
          focusElement = createText(propertyName, 42);
        }
        td.append(focusElement).appendTo(tr);
      }
      
      // Add the remove button
      td = $.create("td").addClass("actions");
      $.create("input").attr("type", "button").val("-")
        .click(function() { removeRow(this, propertyName); })
        .appendTo(td);
      tr.append(td);
      
      if (!tbody.length) {
        tbody = $.create("tbody");
        
        // Find the insertion point for the new row. We try to keep the filter
        // rows in the same order as the options in the 'Add filter' drop-down,
        // because that's the order they'll appear in when submitted
        var insertionPoint = $(this).closest("tbody");
        outer:
        for (var i = this.selectedIndex + 1; i < this.options.length; i++) {
          for (var j = 0; j < table.tBodies.length; j++) {
            if (table.tBodies[j].rows[0].className == this.options[i].value) {
              insertionPoint = $(table.tBodies[j]);
              break outer;
            }
          }
        }
        insertionPoint.before(tbody);
      }
      tbody.append(tr);
      
      if(focusElement)
          focusElement.focus();
      
      // Disable the add filter in the drop-down list
      if (property.type == "radio" || property.type == "checkbox"
          || property.type == "id")
        this.options[this.selectedIndex].disabled = true;
      
      this.selectedIndex = 0;
    }).next("input[name^=add_]").remove();
    
    // Add a new empty clause at the end by cloning the current last clause
    function addClause(button) {
      var tbody = $(button).closest("tbody");
      var clauseNum = parseInt($("select[name^=add_filter_]", tbody)
                               .attr("name").split("_").pop()) + 1;
      tbody = tbody.parents("tbody").eq(0);
      var copy = tbody.clone(true);
      $(button).closest("td").next().attr("colSpan", 4).end().remove();
      $("td.trac-clause-sep", copy).parent().removeAttr("style");
      $("tr tbody:not(:last)", copy).remove();
      var newId = "add_filter_" + clauseNum;
      $("select", copy).attr("id", newId).attr("name", newId)
        .children().enable().end()
        .prev().attr("for", newId);
      tbody.after(copy);
    }
    
    // Make the button for adding a clause a client-side trigger
    var add_clause = $("#filters input[name=add_clause]");
    add_clause.replaceWith($.create("input").attr("type", "button")
                            .attr("id", "add_clause").val(add_clause.val())
                            .click(function() {
      addClause(this);
      return false;
    }));
  }

})(jQuery);
